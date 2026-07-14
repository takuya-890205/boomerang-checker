"""弁護人レビュー（adversarial verification）モジュール

検出されたブーメラン候補を、切り抜き・誤読でないか「政治家側の弁護人」の
視点で再検証する。前後の質疑文脈と発言者の肩書を材料に、
- misread: 指摘自体が誤読（他者発言の引用・仮定の話等）→ 棄却
- explainable: 発言は事実だが状況変化・立場の違いで説明がつく → 言い分付きで保持
- confirmed: 弁護を検討しても矛盾という評価が妥当 → 確定
に振り分ける。SNS公開対象は confirmed のみとする（切り抜き批判への防御層）。
"""

from __future__ import annotations

import time

from boomerang.analyzer import BoomerangResult, generate_json
from boomerang.kokkai_api import (
	REQUEST_INTERVAL,
	Speech,
	fetch_context_speeches,
	truncate_speech,
)

# 弁護人レビューで各発言本文をどこまで渡すか（検出時の800字より長く取り、文脈を保つ）
SPEECH_MAX_CHARS = 2500
CONTEXT_MAX_CHARS = 400  # 前後の発言1件あたり

VALID_VERDICTS = {"misread", "explainable", "confirmed"}


def _format_speech_block(label: str, speech: Speech, context: list[Speech]) -> str:
	"""弁護人プロンプト用に、発言本文＋前後の質疑文脈を整形する"""
	position = speech.speaker_position or "不明"
	group = speech.speaker_group or "不明"
	lines = [
		f"## 発言{label}（{speech.date} {speech.name_of_house} {speech.name_of_meeting}）",
		f"発言者の肩書: {position} / 所属会派: {group}",
		"",
		truncate_speech(speech.speech_text, max_chars=SPEECH_MAX_CHARS),
	]

	if context:
		lines.append("")
		lines.append(f"### 発言{label}の前後の質疑（文脈）")
		for c in context:
			marker = "（前）" if c.speech_order < speech.speech_order else "（後）"
			who = f"{c.speaker}（{c.speaker_position}）" if c.speaker_position else c.speaker
			lines.append(f"{marker}[{who}] {truncate_speech(c.speech_text, max_chars=CONTEXT_MAX_CHARS)}")

	return "\n".join(lines)


def _build_defense_prompt(
	speaker: str,
	result: BoomerangResult,
	context_a: list[Speech],
	context_b: list[Speech],
) -> str:
	"""弁護人レビュー用プロンプトを構築する"""
	block_a = _format_speech_block("A", result.speech_a, context_a)
	block_b = _format_speech_block("B", result.speech_b, context_b)

	return f"""あなたは政治家「{speaker}」の弁護人です。
以下の2つの国会発言について「矛盾している（ブーメラン発言だ）」という指摘がなされています。
あなたの仕事は、この指摘に反論できるかを全力で検討することです。

## 指摘内容
- 発言Aの主張: {result.summary_a}
- 発言Bの主張: {result.summary_b}
- 矛盾の説明: {result.contradiction}

{block_a}

{block_b}

## 検討すべき反論の観点
1. 引用・代読: 指摘された部分は、他者の質問・主張を読み上げたり引用したりしているだけではないか
2. 仮定・反語: 仮定の話や反語表現・懸念の表明を、本人の主張と誤読していないか
3. 立場の違い: 大臣等の役職としての政府見解の答弁と、個人・党としての見解表明を混同していないか
4. 状況の変化: 2つの発言の間に、方針転換を正当化しうる重大な状況変化（災害・感染症・経済危機・国際情勢・法改正等）があったか
5. 部分修正: 主張の細部を調整しただけで、根本的な立場は一貫していないか
6. 文脈の切り取り: 前後の質疑を踏まえると、指摘とは異なる意味の発言ではないか

## 出力形式（JSONのみ）
```json
{{
  "verdict": "misread",
  "defense": "政治家側の言い分（150文字以内）",
  "reason": "verdict の判定理由（100文字以内）"
}}
```

- verdict は次の3値のいずれか:
  - "misread": 指摘自体が誤読（引用・仮定の誤認等で、そもそも矛盾と言えない）
  - "explainable": 両発言とも本人の主張だが、状況変化・立場の違い等で立場変更に相応の説明がつく
  - "confirmed": 上記の反論をすべて検討しても、矛盾・ブーメランという評価が妥当
- defense には政治家側の最も説得力のある言い分を書くこと。confirmed の場合も「どの弁護を検討し、なぜ成立しなかったか」を書くこと
- 判定は厳格に。安易に confirmed とせず、正当な説明が成り立つなら explainable を選ぶこと
- JSON以外は出力しないでください"""


def verify_results(
	speaker: str,
	results: list[BoomerangResult],
	api_key: str | None = None,
	fetch_context: bool = True,
) -> list[BoomerangResult]:
	"""検出結果を弁護人レビューにかけ、生き残ったものだけ返す。

	Args:
		speaker: 議員名
		results: analyze_speeches の検出結果
		api_key: Gemini API キー（省略時は環境変数 GEMINI_API_KEY を使用）
		fetch_context: True のとき国会APIから前後の質疑文脈を取得して判定材料にする

	Returns:
		misread と判定されたペアを除いた BoomerangResult のリスト
		（verdict / defense フィールドが設定される）
	"""
	survived: list[BoomerangResult] = []

	for i, r in enumerate(results, 1):
		print(f"  ⚖️  弁護人レビュー {i}/{len(results)} を実施中...")

		context_a: list[Speech] = []
		context_b: list[Speech] = []
		if fetch_context:
			context_a = fetch_context_speeches(r.speech_a)
			time.sleep(REQUEST_INTERVAL)
			context_b = fetch_context_speeches(r.speech_b)
			time.sleep(REQUEST_INTERVAL)
			if not context_a and not context_b:
				print("    （前後の質疑文脈は取得できませんでした。本文のみで判定します）")

		prompt = _build_defense_prompt(speaker, r, context_a, context_b)

		try:
			data = generate_json(prompt, api_key=api_key)
		except Exception as e:
			# レビュー自体の失敗は「未検証」として保持する（公開判定 is_publishable は
			# verdict=confirmed を要求するため、未検証のままSNSに出ることはない）
			print(f"    ⚠️ レビューに失敗したため未検証のまま保持します: {e}")
			survived.append(r)
			continue

		verdict = str(data.get("verdict", "")).strip() if isinstance(data, dict) else ""
		defense = str(data.get("defense", "")).strip() if isinstance(data, dict) else ""

		if verdict not in VALID_VERDICTS:
			print(f"    ⚠️ 不正な判定値（{verdict!r}）のため未検証のまま保持します")
			survived.append(r)
			continue

		if verdict == "misread":
			print(f"    ❌ 誤読と判定 → 棄却: {defense[:60]}")
			continue

		r.verdict = verdict
		r.defense = defense
		label = "🔥 矛盾確定" if verdict == "confirmed" else "🛡️ 説明可能（言い分あり）"
		print(f"    {label}")
		survived.append(r)

	return survived
