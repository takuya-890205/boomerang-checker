"""Claude API を使ったブーメラン（矛盾）発言検出モジュール"""

from __future__ import annotations

import json
from dataclasses import dataclass

import anthropic

from boomerang.kokkai_api import Speech, truncate_speech

ANALYSIS_MODEL = "claude-sonnet-4-6"


@dataclass
class BoomerangResult:
    """ブーメラン検出結果"""

    speech_a: Speech
    speech_b: Speech
    summary_a: str  # 発言Aの要約
    summary_b: str  # 発言Bの要約
    contradiction: str  # 矛盾の説明
    score: int  # 矛盾スコア（0-100）


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
        api_key: Anthropic API キー（省略時は環境変数 ANTHROPIC_API_KEY を使用）

    Returns:
        BoomerangResult のリスト
    """
    if len(speeches) < 2:
        return []

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    prompt = _build_prompt(speaker, speeches)

    message = client.messages.create(
        model=ANALYSIS_MODEL,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text.strip()

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
