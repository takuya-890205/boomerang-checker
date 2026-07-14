"""公式サイト全文ページのソース取り込みモジュール

官邸・省庁の会見録、政党公式サイトの党首会見録・党大会演説全文など、
「切り取られる前の全文」が公式に公開されているページをURL指定で取り込み、
Speech として検出パイプラインに流す。

報道記事は取り込まない想定（記事は発言の存在を知るためのインデックスに使い、
引用の錨は必ず全文ページに打つ、という2層設計の引用層）。
ページが本当に公式・全文かはツールでは判定できないため、格付けは B とし、
出典URLを常に表示して読者が確認できる形を保つ。
"""

from __future__ import annotations

import re

import requests

from boomerang.kokkai_api import Speech

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) boomerang-checker/1.0"

# 全角数字→半角の変換テーブル（公式ページの日付表記ゆれ対策）
_ZEN2HAN = str.maketrans("０１２３４５６７８９", "0123456789")


def _strip_html(html: str) -> str:
	"""HTMLからテキストを抽出する（script/style除去→タグ除去→空白正規化）"""
	html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.DOTALL | re.IGNORECASE)
	html = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", html, flags=re.IGNORECASE)
	text = re.sub(r"<[^>]+>", " ", html)
	# HTML実体参照の最低限の復元
	for ent, ch in (("&amp;", "&"), ("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&nbsp;", " "), ("&#39;", "'")):
		text = text.replace(ent, ch)
	# 行内の連続空白を潰しつつ、行構造は残す
	lines = [re.sub(r"[ \t　]+", " ", ln).strip() for ln in text.splitlines()]
	return "\n".join(ln for ln in lines if ln)


def _extract_date(text: str) -> str:
	"""ページ本文から日付（YYYY-MM-DD）を推定する。見つからなければ空文字。"""
	t = text[:3000].translate(_ZEN2HAN)  # 日付はページ冒頭にあることが多い

	# 西暦表記（2025年11月4日）
	m = re.search(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", t)
	if m:
		return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

	# 令和表記（令和7年11月4日）: 令和N年 = 2018+N 年
	m = re.search(r"令和\s*(\d{1,2})\s*年\s*(\d{1,2})月\s*(\d{1,2})日", t)
	if m:
		year = 2018 + int(m.group(1))
		return f"{year}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

	return ""


def parse_source_url_arg(arg: str) -> tuple[str, str]:
	"""--source-url の引数をパースする。

	"YYYY-MM-DD:https://..." 形式なら日付を分離、そうでなければURLのみ
	（日付はページから自動推定を試みる）。

	Returns:
		(url, date)  date は空文字の場合あり
	"""
	m = re.match(r"^(\d{4}-\d{2}-\d{2}):(https?://.+)$", arg)
	if m:
		return m.group(2), m.group(1)
	return arg, ""


def fetch_web_source(
	speaker_name: str,
	url: str,
	date: str = "",
	label: str = "公式全文ページ",
) -> Speech | None:
	"""全文ページを1件取得して Speech に変換する。

	Args:
		speaker_name: 議員名
		url: 全文ページのURL
		date: 発言日（YYYY-MM-DD。空ならページから自動推定）
		label: 出典の表示名（会議名の欄に入る）

	Returns:
		Speech（source="Web全文", grade="B"）。取得失敗時は None。
	"""
	try:
		response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
		response.raise_for_status()
		# 文字化け対策: ヘッダにcharsetが無い場合は内容から推定させる
		if not response.encoding or response.encoding.lower() == "iso-8859-1":
			response.encoding = response.apparent_encoding
		text = _strip_html(response.text)
	except requests.RequestException as e:
		print(f"  ⚠️ 全文ページの取得に失敗しました: {url} ({e})")
		return None

	if len(text) < 200:
		print(f"  ⚠️ 抽出テキストが短すぎるためスキップします（{len(text)}字）: {url}")
		return None

	resolved_date = date or _extract_date(text)
	if not resolved_date:
		print(f"  ⚠️ 発言日を特定できませんでした（--source-url 'YYYY-MM-DD:URL' 形式で指定できます）: {url}")

	return Speech(
		speaker=speaker_name,
		date=resolved_date,
		name_of_house="",
		name_of_meeting=label,
		speech_text=text,
		speech_url=url,
		session=0,
		source="Web全文",
		source_grade="B",
	)
