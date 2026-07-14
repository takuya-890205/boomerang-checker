"""公式会見録の発見層（一覧ページ→会見録URLの列挙→全文取り込み）

web_source.py が「URLを1本ずつ手で指定して取り込む」層なのに対し、
本モジュールは公式サイトの一覧ページをスクレイプして会見録URLを自動列挙し、
まとめて Speech として検出パイプラインに流す発見層。

対応サイトはレジストリ方式で追加する（現在: 首相官邸。政党公式サイトを順次追加予定）。

設計メモ:
- 官邸サイトは「現職＋過去3代」までしか保持しない。それ以前は国立国会図書館WARP
  （warp.ndl.go.jp）に恒久アーカイブされており、WARP経由の取り込みは今後の拡張候補。
- 現内閣の代数（/jp/105/ など）はトップページから自動発見する（政権交代で壊れない）。
- 引用の錨は必ず公式全文ページ（グレードB）。報道記事は取り込まない。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

import requests

from boomerang.kokkai_api import Speech
from boomerang.web_source import fetch_web_source, parse_jp_date

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) boomerang-checker/1.0"

KANTEI_BASE = "https://www.kantei.go.jp"
JIMIN_BASE = "https://www.jimin.jp"

# 対応サイト一覧（CLIの --kaiken の選択肢。追加したらここに登録する）
# 注: 立憲民主党の会見ページは党広報の要約記事（全文書き起こしではない）ため
#     引用の錨には使えず未対応。立憲はYouTubeノーカット動画の文字起こしでカバー予定。
SITES: dict[str, str] = {
	"kantei": "首相官邸（総理の演説・記者会見など）",
	"jimin": "自民党（役員記者会見）",
}

# 連続アクセスの間隔（秒）。公式サイトへの礼儀
_FETCH_INTERVAL = 0.7


@dataclass
class KaikenEntry:
	"""一覧ページから列挙した会見録1件分のメタデータ"""

	title: str
	date: str  # YYYY-MM-DD（変換できなければ空文字）
	url: str  # 絶対URL


def _fetch_html(url: str) -> str:
	"""一覧ページのHTMLを取得する。失敗時は空文字。"""
	try:
		response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
		response.raise_for_status()
		if not response.encoding or response.encoding.lower() == "iso-8859-1":
			response.encoding = response.apparent_encoding
		return response.text
	except requests.RequestException as e:
		print(f"  ⚠️ 一覧ページの取得に失敗しました: {url} ({e})")
		return ""


def _detect_current_cabinet() -> int | None:
	"""官邸トップページから現内閣の代数（/jp/105/ の 105）を自動発見する。"""
	html = _fetch_html(f"{KANTEI_BASE}/")
	m = re.search(r"/jp/(\d+)/statement/", html)
	return int(m.group(1)) if m else None


def list_kantei_kaiken(cabinet: int | None = None, year: int | None = None) -> list[KaikenEntry]:
	"""官邸「総理の演説・記者会見など」の一覧から会見録URLを列挙する。

	Args:
		cabinet: 内閣の代数（例: 105）。省略時はトップページから現内閣を自動発見
		year: 西暦年。指定すると年別アーカイブ（全件）、省略時は最新20件のページ

	Returns:
		KaikenEntry のリスト（一覧ページの掲載順＝新しい順）
	"""
	if cabinet is None:
		cabinet = _detect_current_cabinet()
		if cabinet is None:
			print("  ⚠️ 官邸トップページから現内閣を特定できませんでした（--kaiken-cabinet で代数を指定できます）")
			return []

	if year:
		index_url = f"{KANTEI_BASE}/jp/{cabinet}/statement/{year}/index.html"
	else:
		index_url = f"{KANTEI_BASE}/jp/{cabinet}/statement/index.html"

	html = _fetch_html(index_url)
	if not html:
		return []

	# 一覧の1件: <li class="list-photo__item"> ... href="..." ... __title">タイトル</p> ... __date">令和8年7月1日</p>
	entries: list[KaikenEntry] = []
	pattern = re.compile(
		r'<li class="list-photo__item">.*?href="([^"]+)".*?'
		r'list-photo__title">([^<]+)</p>.*?'
		r'list-photo__date">([^<]+)</p>',
		re.DOTALL,
	)
	for m in pattern.finditer(html):
		href, title, date_text = m.group(1), m.group(2).strip(), m.group(3).strip()
		url = href if href.startswith("http") else KANTEI_BASE + href
		entries.append(KaikenEntry(title=title, date=parse_jp_date(date_text), url=url))

	if not entries:
		print(f"  ⚠️ 一覧から会見録を抽出できませんでした（ページ構造が変わった可能性）: {index_url}")
	return entries


def list_jimin_kaiken(year: int | None = None) -> list[KaikenEntry]:
	"""自民党サイトの記者会見（役員会見・ぶら下がり等）を列挙する。

	一覧ページはJS描画だが、データ元は年別の静的JSON
	（/news/data/<YYYY>_all.json）なので、それを直接取得して
	category に "press" を含む記事だけを抽出する。
	個別ページ（/news/press/NNNNNN.html）は一問一答の全文書き起こし。

	Args:
		year: 西暦年。省略時は今年のJSONを使う
	"""
	if year is None:
		# 最新一覧＝今年のJSON。年をまたいだ直後も動くよう、空なら前年へフォールバック
		from datetime import date as _date

		year = _date.today().year

	json_url = f"{JIMIN_BASE}/news/data/{year}_all.json"
	try:
		response = requests.get(json_url, headers={"User-Agent": USER_AGENT}, timeout=30)
		response.raise_for_status()
		articles = response.json()
	except (requests.RequestException, ValueError) as e:
		print(f"  ⚠️ 自民党ニュースJSONの取得に失敗しました: {json_url} ({e})")
		return []

	entries: list[KaikenEntry] = []
	for article in articles:
		if "press" not in article.get("category", []):
			continue
		url = article.get("url", "")
		if not url:
			continue
		date = (article.get("release") or "")[:10]  # "2026-07-13 18:00:00" → "2026-07-13"
		entries.append(
			KaikenEntry(
				title=article.get("title", "").strip(),
				date=date,
				url=url if url.startswith("http") else JIMIN_BASE + url,
			)
		)

	if not entries:
		print(f"  ⚠️ {year}年の自民党記者会見が見つかりませんでした: {json_url}")
	return entries


def fetch_kaiken_speeches(
	speaker_name: str,
	site: str = "kantei",
	keyword: str | None = None,
	limit: int = 10,
	year: int | None = None,
	cabinet: int | None = None,
) -> list[Speech]:
	"""公式会見録を自動発見して Speech のリストに変換する。

	一覧から新しい順に最大 limit 件の全文ページを取得し、
	(1) 発言者の姓が本文に含まれない（別人の会見）、
	(2) keyword 指定時に本文へ keyword が含まれない、
	ものを除外して返す。

	Args:
		speaker_name: 議員名（例: 高市早苗）
		site: 対応サイト名（SITES のキー）
		keyword: 争点キーワード（本文フィルタ。タイトルに無い質疑中の言及も拾う）
		limit: 全文を取得する最大ページ数（一覧の新しい順）
		year: 西暦年（年別アーカイブから列挙）
		cabinet: 内閣の代数（官邸のみ。省略時は現内閣を自動発見）
	"""
	if site == "kantei":
		entries = list_kantei_kaiken(cabinet=cabinet, year=year)
	elif site == "jimin":
		entries = list_jimin_kaiken(year=year)
	else:
		print(f"  ⚠️ 未対応のサイトです: {site}（対応: {', '.join(SITES)}）")
		return []

	if not entries:
		return []

	# 発言者ガード: 姓（先頭2文字）または氏名そのものが本文に無いページは別人の発言とみなす
	surname = speaker_name[:2]

	# タイトルに姓が含まれるエントリを優先して limit 件選ぶ（自民党は「鈴木幹事長記者会見」等、
	# タイトルで発言者が分かる。官邸はタイトルに総理名が入らないため新しい順のまま）
	titled = [e for e in entries if surname in e.title]
	others = [e for e in entries if surname not in e.title]
	candidates = (titled + others)[:limit] if titled else entries[:limit]
	if titled:
		print(f"  📋 一覧から{len(entries)}件を発見（うちタイトルに「{surname}」を含む{len(titled)}件を優先）。最大{limit}件の全文を取得します。")
	else:
		print(f"  📋 一覧から{len(entries)}件を発見。新しい順に最大{limit}件の全文を取得します。")

	speeches: list[Speech] = []
	skipped_speaker = 0
	skipped_keyword = 0
	for entry in candidates:
		speech = fetch_web_source(
			speaker_name=speaker_name,
			url=entry.url,
			date=entry.date,
			label=f"{SITES[site]}・{entry.title}",
		)
		time.sleep(_FETCH_INTERVAL)
		if speech is None:
			continue
		if surname not in speech.speech_text and speaker_name not in speech.speech_text:
			skipped_speaker += 1
			continue
		if keyword and keyword not in speech.speech_text:
			skipped_keyword += 1
			continue
		speeches.append(speech)

	if skipped_speaker:
		print(f"  ℹ️ 発言者名（{surname}）が本文に無い{skipped_speaker}件を除外しました。")
	if skipped_keyword:
		print(f"  ℹ️ キーワード（{keyword}）が本文に無い{skipped_keyword}件を除外しました。")
	return speeches
