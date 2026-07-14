"""Gemini API を使ったブーメラン（矛盾）発言検出モジュール"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from google import genai
from google.genai import errors as genai_errors

from boomerang.kokkai_api import Speech, truncate_speech

ANALYSIS_MODEL = "gemini-2.5-flash"

# 無料枠の制限（gemini-2.5-flash）
FREE_TIER_RPD = 1500   # 1日あたりの最大リクエスト数
FREE_TIER_RPM = 10     # 1分あたりの最大リクエスト数
FREE_TIER_TPM = 1_000_000  # 1分あたりの最大トークン数

# 安全マージン（無料枠の80%まで使用）
DAILY_REQUEST_LIMIT = int(FREE_TIER_RPD * 0.8)   # 1200リクエスト/日
TOKEN_LIMIT_PER_REQUEST = int(FREE_TIER_TPM * 0.8)  # 800,000トークン/リクエスト

# 日次カウンターファイルのパス
COUNTER_FILE = Path.home() / ".cache" / "boomerang_checker" / "daily_counter.json"

# リトライ設定
MAX_RETRIES = 3
RETRY_BASE_WAIT = 20  # 初回リトライ待機秒数


@dataclass
class BoomerangResult:
	"""ブーメラン検出結果"""

	speech_a: Speech
	speech_b: Speech
	summary_a: str  # 発言Aの要約
	summary_b: str  # 発言Bの要約
	contradiction: str  # 矛盾の説明
	score: int  # 矛盾スコア（0-100）
	year_gap: int = 0  # 発言年の差の絶対値
	excerpt_a: str = ""  # 発言Aの原文抜粋（LLM出力）
	excerpt_b: str = ""  # 発言Bの原文抜粋（LLM出力）
	excerpt_a_verified: bool = False  # 抜粋Aが原文と一致するか（機械検証）
	excerpt_b_verified: bool = False  # 抜粋Bが原文と一致するか（機械検証）
	verdict: str = ""  # 弁護人レビューの判定（confirmed/explainable。未実施は空）
	defense: str = ""  # 政治家側の言い分（弁護人レビューの出力）

	@property
	def publish_tier(self) -> str:
		"""公開階層を返す。

		- "confirmed": 弁護人レビューでも矛盾が確定 → 断定調で公開可
		- "gray": 説明可能と判定 → 想定される言い分を併記する形でのみ公開可
		- "": 非公開（未検証・誤読・原文一致が取れていない）

		いずれの公開階層も原文一致の機械検証を通過していることが前提。
		"""
		if not (self.excerpt_a_verified and self.excerpt_b_verified):
			return ""
		if self.verdict == "confirmed":
			return "confirmed"
		if self.verdict == "explainable":
			return "gray"
		return ""

	@property
	def is_publishable(self) -> bool:
		"""何らかの形でSNS公開に耐えるか（断定調 or 言い分併記）"""
		return bool(self.publish_tier)


def _estimate_tokens(text: str) -> int:
	"""テキストのトークン数を推定する（日本語: 約1.5文字/トークン）"""
	return int(len(text) / 1.5)


def _load_counter() -> dict:
	"""日次リクエストカウンターを読み込む"""
	if not COUNTER_FILE.exists():
		return {"date": "", "count": 0}
	try:
		return json.loads(COUNTER_FILE.read_text(encoding="utf-8"))
	except (json.JSONDecodeError, OSError):
		return {"date": "", "count": 0}


def _save_counter(counter: dict) -> None:
	"""日次リクエストカウンターを保存する"""
	COUNTER_FILE.parent.mkdir(parents=True, exist_ok=True)
	COUNTER_FILE.write_text(json.dumps(counter), encoding="utf-8")


def _increment_and_check_daily_limit() -> int:
	"""日次リクエスト数をインクリメントし、残り回数を返す。上限超過時は例外を送出する。"""
	today = date.today().isoformat()
	counter = _load_counter()

	# 日付が変わったらリセット
	if counter["date"] != today:
		counter = {"date": today, "count": 0}

	counter["count"] += 1
	_save_counter(counter)

	remaining = DAILY_REQUEST_LIMIT - counter["count"]
	if remaining < 0:
		raise RuntimeError(
			f"1日のリクエスト上限（{DAILY_REQUEST_LIMIT}回）に達しました。"
			f"明日またお試しください。（無料枠保護のため）"
		)
	return remaining


def _trim_speeches_to_token_budget(
	speaker: str,
	speeches: list[Speech],
	keyword: str | None = None,
) -> list[Speech]:
	"""トークン予算内に収まるよう発言数を削減する"""
	# システムプロンプト部分の固定トークンを差し引く
	fixed_tokens = _estimate_tokens(speaker) + 500
	budget = TOKEN_LIMIT_PER_REQUEST - fixed_tokens

	trimmed: list[Speech] = []
	used_tokens = 0
	for s in speeches:
		text = truncate_speech(s.speech_text, max_chars=800, keyword=keyword)
		tokens = _estimate_tokens(text) + 50  # 区切り文字等のオーバーヘッド
		if used_tokens + tokens > budget:
			break
		trimmed.append(s)
		used_tokens += tokens

	if len(trimmed) < len(speeches):
		print(
			f"  ⚠️  トークン上限のため発言数を {len(speeches)} → {len(trimmed)} 件に削減しました"
			f"（推定 {used_tokens:,} トークン使用）"
		)
	return trimmed


# 断片がこの長さ未満だと偶然一致しうるため検証を通さない
MIN_FRAGMENT_CHARS = 8


def _relaxed_stream(text: str) -> tuple[str, list[int]]:
	"""照合用の緩い正規化ストリームと、原文位置への対応表を作る。

	NFKC正規化のうえ、空白と句読点・記号を除去する（LLM抜粋の句読点レベルの
	揺れを吸収するため）。文字内容そのものは変えないので、8文字以上の
	日本語断片が偶然一致することは実用上ない。
	"""
	import re
	import unicodedata

	stream_chars: list[str] = []
	index_map: list[int] = []
	# HTMLタグは範囲ごと除去（原文位置の対応を保つためタグ内はスキップ）
	tag_spans = [(m.start(), m.end()) for m in re.finditer(r"<[^>]+>", text)]

	def in_tag(i: int) -> bool:
		return any(s <= i < e for s, e in tag_spans)

	for i, ch in enumerate(text):
		if in_tag(i):
			continue
		for nch in unicodedata.normalize("NFKC", ch):
			if nch.isspace():
				continue
			cat = unicodedata.category(nch)
			if cat.startswith("P") or cat.startswith("S"):
				continue  # 句読点・記号は照合対象から除外
			stream_chars.append(nch)
			index_map.append(i)

	return "".join(stream_chars), index_map


def match_excerpt(excerpt: str, original_text: str) -> str | None:
	"""LLMが返した抜粋を原文と照合し、一致すれば**原文どおりの抜粋**を返す。

	- 「…」「（中略）」「...」で明示的に省略された抜粋は断片ごとに分割し、
	  全断片が原文に抜粋と同じ順序で実在する場合のみ合格
	  （透明な省略は許容し、捏造・順序の入れ替えは拒否する）
	- 照合は句読点・空白・全角半角を無視して行い、合格時は表示用の抜粋を
	  LLM出力ではなく**原文の該当範囲そのもの**に置き換える（改変引用の混入防止）
	- 不合格なら None
	"""
	import re

	if not excerpt:
		return None

	stream, index_map = _relaxed_stream(original_text)
	fragments = [f for f in re.split(r"…|（中略）|\.\.\.", excerpt) if f.strip()]
	if not fragments:
		return None

	repaired: list[str] = []
	pos = 0
	for fragment in fragments:
		frag_stream, _ = _relaxed_stream(fragment)
		if len(frag_stream) < MIN_FRAGMENT_CHARS:
			return None  # 短すぎる断片は偶然一致しうるため不合格
		hit = _find_fragment(stream, frag_stream, pos)
		if hit is None:
			return None
		found, core_len = hit
		start = index_map[found]
		end = index_map[found + core_len - 1]
		# 原文の該当範囲を採用（改行等の空白だけは1スペースに正規化）
		repaired.append(" ".join(original_text[start:end + 1].split()))
		pos = found + core_len

	return "…".join(repaired)


# 断片の端の削り許容量（LLMが語尾・接続詞を付け足す軽微な揺れの吸収用。
# 中身の改変は救わない＝断片の中央部が原文に無ければ不合格のまま）
MAX_TRIM = 8


def _find_fragment(stream: str, frag: str, pos: int) -> tuple[int, int] | None:
	"""断片を原文ストリームから探す。端のトリムを許容し、(位置, コア長) を返す。

	完全一致を最優先し、失敗時は先頭・末尾を合計 MAX_TRIM 文字（かつ断片の
	25%以内）まで削った「コア」での一致を、削り量の少ない順に試す。
	"""
	found = stream.find(frag, pos)
	if found >= 0:
		return found, len(frag)

	max_trim = min(MAX_TRIM, len(frag) // 4)
	for total in range(1, max_trim + 1):
		for head in range(total + 1):
			tail = total - head
			core = frag[head:len(frag) - tail] if tail else frag[head:]
			if len(core) < MIN_FRAGMENT_CHARS:
				continue
			found = stream.find(core, pos)
			if found >= 0:
				return found, len(core)
	return None


def verify_excerpt(excerpt: str, original_text: str) -> bool:
	"""抜粋が原文に実在するかの真偽だけを返す（match_excerpt の簡易版）"""
	return match_excerpt(excerpt, original_text) is not None


def _anchor_speech(
	label: str,
	speeches: list[Speech],
	idx: int,
	excerpt: str,
) -> tuple[Speech, str, bool]:
	"""抜粋の原文照合を行い、必要なら正しい発言へ再アンカーする。

	LLMが発言インデックスを取り違えても、原文抜粋が実在する発言が一意に見つかれば
	そちらを採用する（抜粋＝一字一句の証拠の方がインデックスより信頼できる）。

	Returns:
		(発言, 抜粋, 抜粋が原文と一致したか)
	"""
	speech = speeches[idx]
	if not excerpt:
		return speech, excerpt, False
	repaired = match_excerpt(excerpt, speech.speech_text)
	if repaired is not None:
		return speech, repaired, True

	matches = [
		(s, rep) for s in speeches
		if (rep := match_excerpt(excerpt, s.speech_text)) is not None
	]
	if len(matches) == 1:
		matched_speech, repaired = matches[0]
		print(f"  🔧 抜粋{label}のインデックスずれを自動補正しました（発言{idx} → {matched_speech.date}の発言）")
		return matched_speech, repaired, True

	print(f"  ⚠️  抜粋{label}が原文と一致しません（改変引用の疑い）: 「{excerpt[:60]}...」")
	return speech, excerpt, False


def generate_json(prompt: str, api_key: str | None = None) -> object:
	"""Gemini にプロンプトを投げ、レスポンスをJSONとしてパースして返す。

	日次リクエスト上限のカウント・429リトライ・コードブロック除去を共通処理する。
	パースに失敗した場合は「矛盾なし」と区別するため例外を送出する。
	"""
	remaining = _increment_and_check_daily_limit()
	print(f"  📊 本日の残りリクエスト数: {remaining} / {DAILY_REQUEST_LIMIT}")

	resolved_key = api_key or os.environ.get("GEMINI_API_KEY")
	client = genai.Client(api_key=resolved_key)

	response = None
	for attempt in range(MAX_RETRIES):
		try:
			response = client.models.generate_content(
				model=ANALYSIS_MODEL,
				contents=prompt,
			)
			break  # 成功したらループを抜ける
		except genai_errors.APIError as e:
			# 429（レート制限）と5xx（サーバー側の一時障害）はリトライ対象
			retryable = e.code == 429 or (e.code is not None and 500 <= e.code < 600)
			if retryable and attempt < MAX_RETRIES - 1:
				wait = RETRY_BASE_WAIT * (2 ** attempt)
				print(f"  ⏳ 一時エラー（{e.code}）。{wait}秒後にリトライします... ({attempt + 1}/{MAX_RETRIES - 1})")
				time.sleep(wait)
			else:
				raise

	response_text = response.text.strip()

	# JSON部分を抽出（```json ... ``` で囲まれている場合も対応）
	if "```json" in response_text:
		response_text = response_text.split("```json")[1].split("```")[0].strip()
	elif "```" in response_text:
		response_text = response_text.split("```")[1].split("```")[0].strip()

	try:
		return json.loads(response_text)
	except json.JSONDecodeError as e:
		raise RuntimeError(
			f"Gemini のレスポンスをJSONとして解釈できませんでした: {e}\n"
			f"--- レスポンス先頭500字 ---\n{response_text[:500]}"
		) from e


def _build_prompt(speaker: str, speeches: list[Speech], keyword: str | None = None) -> str:
	"""分析用プロンプトを構築する"""
	speech_entries = []
	for i, s in enumerate(speeches):
		# 争点キーワード指定時は出現箇所を中心に切り出す（長い演説の奥にある言及を落とさない）
		text = truncate_speech(s.speech_text, max_chars=800, keyword=keyword)
		year = s.date[:4] if s.date else "不明"
		# ラベルは0始まり（JSONで返させる speech_a_index と揃える。ずれると原文照合が全滅する）
		speech_entries.append(
			f"[発言{i}] ({year}年 {s.name_of_house} {s.name_of_meeting})\n{text}"
		)

	speeches_text = "\n\n---\n\n".join(speech_entries)

	keyword_note = (
		f"\n特に争点「{keyword}」に対する立場・主張の変化に注目してください。\n"
		if keyword
		else ""
	)

	return f"""あなたは国会発言の矛盾分析の専門家です。
以下は「{speaker}」議員の国会での発言一覧です。

{speeches_text}

上記の発言群から、互いに矛盾する発言ペア（ブーメラン発言）を最大5組検出してください。
同じ議員が過去に主張していたことと正反対のことを言っている、または過去に批判していたことを自分がやっている、というケースを探してください。
{keyword_note}

以下のJSON形式で出力してください。矛盾が見つからない場合は空配列を返してください。

```json
[
  {{
    "speech_a_index": 0,
    "speech_b_index": 1,
    "summary_a": "発言Aの要約（50文字以内）",
    "summary_b": "発言Bの要約（50文字以内）",
    "excerpt_a": "発言Aの原文からの一字一句そのままの抜粋（矛盾の核心部分、20〜100文字）",
    "excerpt_b": "発言Bの原文からの一字一句そのままの抜粋（矛盾の核心部分、20〜100文字）",
    "contradiction": "矛盾の説明（100文字以内）",
    "score": 85,
    "year_gap": 5
  }}
]
```

注意:
- speech_a_index, speech_b_index は [発言N] の N をそのまま使ってください（0始まり）
- excerpt_a / excerpt_b は上記の発言本文から**一字一句改変せずコピー**してください（要約・言い換え・省略記号の挿入は禁止。後段で原文との完全一致を機械検証します）
- 発言本文中の「…」や「（中略）」は表示上の省略記号です。これらを**またいだ抜粋・含んだ抜粋は禁止**（連続した本文の範囲からのみ抜粋すること）
- score は矛盾の度合い（0-100）。高いほど明確な矛盾
- 70点以上のものだけ報告してください
- 発言の時系列を考慮し、古い発言をA、新しい発言をBとしてください
- 発言の年が3年以上離れているペアを優先的に報告してください
- 同一会期内の矛盾よりも、年代をまたぐ矛盾（例：政権交代前後、選挙前後）を重視してください
- JSONの各ペアに "year_gap" フィールド（発言年の差の絶対値）を追加してください
- 他者の発言の引用・読み上げ、仮定の話を本人の主張と取り違えないでください
- JSON以外は出力しないでください"""


def analyze_speeches(
	speaker: str,
	speeches: list[Speech],
	api_key: str | None = None,
	keyword: str | None = None,
) -> list[BoomerangResult]:
	"""発言リストからブーメラン発言を検出する。

	Args:
		speaker: 議員名
		speeches: 発言リスト
		api_key: Gemini API キー（省略時は環境変数 GEMINI_API_KEY を使用）
		keyword: 争点キーワード（指定時はその争点への立場変化に注目させる）

	Returns:
		BoomerangResult のリスト
	"""
	if len(speeches) < 2:
		return []

	# トークン予算に合わせて発言数を調整
	speeches = _trim_speeches_to_token_budget(speaker, speeches, keyword=keyword)

	prompt = _build_prompt(speaker, speeches, keyword=keyword)
	estimated_tokens = _estimate_tokens(prompt)
	print(f"  📝 推定入力トークン数: {estimated_tokens:,}")

	results_data = generate_json(prompt, api_key=api_key)
	if not isinstance(results_data, list):
		raise RuntimeError(f"Gemini のレスポンスがリスト形式ではありません: {type(results_data)}")

	results: list[BoomerangResult] = []
	for item in results_data:
		idx_a = item.get("speech_a_index", 0)
		idx_b = item.get("speech_b_index", 1)
		if idx_a >= len(speeches) or idx_b >= len(speeches):
			continue

		# 原文抜粋の機械検証（LLMの捏造・改変引用をここで検出する）。
		# 主張されたインデックスで一致しない場合は、抜粋の実在箇所を全発言から探して
		# 再アンカーする（原文抜粋はインデックスより信頼できる証拠のため）
		speech_a, excerpt_a, excerpt_a_verified = _anchor_speech(
			"A", speeches, idx_a, item.get("excerpt_a", "")
		)
		speech_b, excerpt_b, excerpt_b_verified = _anchor_speech(
			"B", speeches, idx_b, item.get("excerpt_b", "")
		)

		summary_a = item.get("summary_a", "")
		summary_b = item.get("summary_b", "")

		# 時系列の正規化（古い方をAにする。プロンプト指示だけに頼らない）
		if speech_a.date and speech_b.date and speech_a.date > speech_b.date:
			speech_a, speech_b = speech_b, speech_a
			summary_a, summary_b = summary_b, summary_a
			excerpt_a, excerpt_b = excerpt_b, excerpt_a
			excerpt_a_verified, excerpt_b_verified = excerpt_b_verified, excerpt_a_verified

		# year_gap は常に発言日付から計算（LLM出力に依存しない）
		year_a = int(speech_a.date[:4]) if speech_a.date and len(speech_a.date) >= 4 else 0
		year_b = int(speech_b.date[:4]) if speech_b.date and len(speech_b.date) >= 4 else 0
		year_gap = abs(year_a - year_b) if year_a and year_b else 0

		results.append(
			BoomerangResult(
				speech_a=speech_a,
				speech_b=speech_b,
				summary_a=summary_a,
				summary_b=summary_b,
				contradiction=item.get("contradiction", ""),
				score=int(item.get("score", 0)),
				year_gap=year_gap,
				excerpt_a=excerpt_a,
				excerpt_b=excerpt_b,
				excerpt_a_verified=excerpt_a_verified,
				excerpt_b_verified=excerpt_b_verified,
			)
		)

	# スコア降順でソート
	results.sort(key=lambda r: r.score, reverse=True)
	return results
