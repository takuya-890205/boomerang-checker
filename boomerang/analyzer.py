"""Gemini API を使ったブーメラン（矛盾）発言検出モジュール"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
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
) -> list[Speech]:
	"""トークン予算内に収まるよう発言数を削減する"""
	# システムプロンプト部分の固定トークンを差し引く
	fixed_tokens = _estimate_tokens(speaker) + 500
	budget = TOKEN_LIMIT_PER_REQUEST - fixed_tokens

	trimmed: list[Speech] = []
	used_tokens = 0
	for s in speeches:
		text = truncate_speech(s.speech_text, max_chars=800)
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


def _build_prompt(speaker: str, speeches: list[Speech]) -> str:
	"""分析用プロンプトを構築する"""
	speech_entries = []
	for i, s in enumerate(speeches):
		text = truncate_speech(s.speech_text, max_chars=800)
		year = s.date[:4] if s.date else "不明"
		speech_entries.append(
			f"[発言{i + 1}] ({year}年 {s.name_of_house} {s.name_of_meeting})\n{text}"
		)

	speeches_text = "\n\n---\n\n".join(speech_entries)

	return f"""あなたは国会発言の矛盾分析の専門家です。
以下は「{speaker}」議員の国会での発言一覧です。

{speeches_text}

上記の発言群から、互いに矛盾する発言ペア（ブーメラン発言）を最大5組検出してください。
同じ議員が過去に主張していたことと正反対のことを言っている、または過去に批判していたことを自分がやっている、というケースを探してください。

以下のJSON形式で出力してください。矛盾が見つからない場合は空配列を返してください。

```json
[
  {{
    "speech_a_index": 0,
    "speech_b_index": 1,
    "summary_a": "発言Aの要約（50文字以内）",
    "summary_b": "発言Bの要約（50文字以内）",
    "contradiction": "矛盾の説明（100文字以内）",
    "score": 85
  }}
]
```

注意:
- speech_a_index, speech_b_index は発言番号（0始まり）
- score は矛盾の度合い（0-100）。高いほど明確な矛盾
- 70点以上のものだけ報告してください
- 発言の時系列を考慮し、古い発言をA、新しい発言をBとしてください
- JSON以外は出力しないでください"""


def analyze_speeches(
	speaker: str,
	speeches: list[Speech],
	api_key: str | None = None,
) -> list[BoomerangResult]:
	"""発言リストからブーメラン発言を検出する。

	Args:
		speaker: 議員名
		speeches: 発言リスト
		api_key: Gemini API キー（省略時は環境変数 GEMINI_API_KEY を使用）

	Returns:
		BoomerangResult のリスト
	"""
	if len(speeches) < 2:
		return []

	# 日次リクエスト上限チェック
	remaining = _increment_and_check_daily_limit()
	print(f"  📊 本日の残りリクエスト数: {remaining} / {DAILY_REQUEST_LIMIT}")

	# トークン予算に合わせて発言数を調整
	speeches = _trim_speeches_to_token_budget(speaker, speeches)

	# APIキーを設定（引数 > 環境変数の順に優先）
	resolved_key = api_key or os.environ.get("GEMINI_API_KEY")
	client = genai.Client(api_key=resolved_key)

	prompt = _build_prompt(speaker, speeches)
	estimated_tokens = _estimate_tokens(prompt)
	print(f"  📝 推定入力トークン数: {estimated_tokens:,}")

	# リトライループ（レート制限対応）
	for attempt in range(MAX_RETRIES):
		try:
			response = client.models.generate_content(
				model=ANALYSIS_MODEL,
				contents=prompt,
			)
			break  # 成功したらループを抜ける
		except genai_errors.ClientError as e:
			if "429" in str(e) and attempt < MAX_RETRIES - 1:
				wait = RETRY_BASE_WAIT * (2 ** attempt)
				print(f"  ⏳ レート制限に達しました。{wait}秒後にリトライします... ({attempt + 1}/{MAX_RETRIES - 1})")
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
		results_data = json.loads(response_text)
	except json.JSONDecodeError:
		return []

	results: list[BoomerangResult] = []
	for item in results_data:
		idx_a = item.get("speech_a_index", 0)
		idx_b = item.get("speech_b_index", 1)
		if idx_a >= len(speeches) or idx_b >= len(speeches):
			continue
		results.append(
			BoomerangResult(
				speech_a=speeches[idx_a],
				speech_b=speeches[idx_b],
				summary_a=item.get("summary_a", ""),
				summary_b=item.get("summary_b", ""),
				contradiction=item.get("contradiction", ""),
				score=int(item.get("score", 0)),
			)
		)

	# スコア降順でソート
	results.sort(key=lambda r: r.score, reverse=True)
	return results
