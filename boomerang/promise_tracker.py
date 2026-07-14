"""約束トラッカー（言 vs 行 の照合）モジュール

「発言 vs 発言」の矛盾検出（analyzer.py）と対をなす、「言ったこと vs やったこと」の
不一致検出。比較の片側を検証可能な事実（行動・結果）にすることで、
言葉遊びに回収されない対比を作る。

型:
- 約束不履行型（言→行）: やると約束した → 現在まで実現していない／今は消極・反対
- 実績否認型（行→言）: 自分が実行した実績がある → 今は逆方向を主張

原則（analyzer と共通）:
- 引用は原文抜粋の機械照合を通ったものだけを表示する
- カードの成立に必要な中間事実は、発言中の根拠（アンカー）が照合できたものだけ
  ✅ とし、照合できないものは ⚠️（機械照合待ち）としてSNS出力から除外する
- 弁護人レビュー（不履行・転向の理由の弁護）を通し、公開3階層で制御する
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from boomerang.analyzer import (
	_anchor_speech,
	_estimate_tokens,
	generate_json,
	verify_excerpt,
)
from boomerang.kokkai_api import (
	REQUEST_INTERVAL,
	Speech,
	fetch_context_speeches,
	truncate_speech,
)

CARD_TYPES = {"約束不履行型", "実績否認型"}
VALID_VERDICTS = {"misread", "explainable", "confirmed"}

# 現在の態度とみなす期間（この日数以内の発言・ポスト）
RECENT_DAYS = 400


@dataclass
class FactLine:
	"""カードの成立に必要な中間事実1件"""

	claim: str  # 事実の主張（例: 45削減法案は廃案になった）
	anchor_speech: Speech | None = None  # 根拠となる発言（照合済みの場合）
	anchor_excerpt: str = ""  # 根拠の原文抜粋
	verified: bool = False  # アンカーが原文照合を通ったか


@dataclass
class PromiseCard:
	"""約束トラッカーの検出結果1件"""

	card_type: str  # 約束不履行型 / 実績否認型
	past_speech: Speech  # 約束 or 実績の発言
	past_excerpt: str
	past_summary: str
	deadline: str  # 約束の期限表現（あれば）
	current_speech: Speech  # 現在の態度
	current_excerpt: str
	current_summary: str
	gap: str  # 対比の一行説明
	score: int
	facts: list[FactLine] = field(default_factory=list)
	past_verified: bool = False
	current_verified: bool = False
	verdict: str = ""  # 弁護人レビュー結果
	defense: str = ""  # 想定される言い分

	@property
	def publish_tier(self) -> str:
		"""公開階層（analyzer.BoomerangResult と同じ3階層）"""
		if not (self.past_verified and self.current_verified):
			return ""
		if self.verdict == "confirmed":
			return "confirmed"
		if self.verdict == "explainable":
			return "gray"
		return ""

	@property
	def verified_facts(self) -> list[FactLine]:
		"""原文アンカーの照合を通った事実だけ（SNS出力用）"""
		return [f for f in self.facts if f.verified]


def _speech_label(s: Speech) -> str:
	"""プロンプト・表示用の発言ラベル（例: 2012年 衆議院 党首討論）"""
	year = s.date[:4] if s.date else "不明"
	venue = f"{s.name_of_house} {s.name_of_meeting}".strip()
	return f"{year}年 {venue}"


def _build_extraction_prompt(
	speaker: str,
	speeches: list[Speech],
	keyword: str | None,
) -> str:
	"""約束・実績の抽出＋現在の態度とのペアリング用プロンプト"""
	entries = []
	for i, s in enumerate(speeches):
		text = truncate_speech(s.speech_text, max_chars=800, keyword=keyword)
		position = f" 発言者肩書:{s.speaker_position}" if s.speaker_position else ""
		entries.append(
			f"[発言{i}] ({s.date} {s.name_of_house} {s.name_of_meeting} 出典:{s.source}{position})\n{text}"
		)
	speeches_text = "\n\n---\n\n".join(entries)
	keyword_note = f"争点は「{keyword}」です。この争点に関する" if keyword else ""

	return f"""あなたは政治家の「言行不一致」を分析する専門家です。
以下は「{speaker}」議員の発言一覧です（国会議事録・本人Xポスト等の一次ソース、日付順とは限りません）。

{speeches_text}

{keyword_note}言行不一致の候補を、次の2つの型で最大4件検出してください。

## 検出する型
1. **約束不履行型**: 過去に「必ずやる」「やり遂げる」「実現する」等の強い実行の言明（できれば期限付き）をしたのに、
   その後実現しないまま、現在は消極的・反対・別の優先順位を主張している
2. **実績否認型**: 過去に自分（自分の政権・政党）が実行・成立させた実績があるのに、
   現在はそれと逆方向の主張をしている

## 比較の作法
- past（約束/実績）は古い発言から、current（現在の態度）は**直近1年程度**の発言・ポストから選ぶこと
- 単なる「発言と発言のニュアンスの違い」は検出しない。**実行の言明・実績**が片側にあるものだけを検出する
- 願望・一般論・他者の発言の引用を「約束」と誤読しないこと

## 出力形式（JSONのみ）
```json
[
  {{
    "card_type": "約束不履行型",
    "past_index": 12,
    "past_excerpt": "過去発言の原文からの一字一句そのままの抜粋（約束/実績の核心、30〜120文字）",
    "past_summary": "何を約束/実行したか（40文字以内）",
    "deadline": "約束の期限表現（原文中にあれば。なければ空文字）",
    "current_index": 3,
    "current_excerpt": "現在の発言の原文からの一字一句そのままの抜粋（30〜120文字）",
    "current_summary": "現在の態度（40文字以内）",
    "gap": "対比の一行説明（〜と言った→〜、の形で60文字以内）",
    "fact_claims": [
      {{
        "claim": "カードの成立に必要な中間事実（例: その後13年間実現していない）",
        "supporting_index": 5,
        "supporting_excerpt": "発言一覧の中に根拠があれば、その原文抜粋（一字一句）。なければ null"
      }}
    ],
    "score": 90
  }}
]
```

注意:
- past_index / current_index は [発言N] の N をそのまま使う（0始まり）
- past_excerpt / current_excerpt / supporting_excerpt は本文から**一字一句改変せずコピー**
  （要約・言い換え禁止。後段で原文との完全一致を機械検証します）
- 本文中の「…」「（中略）」は表示上の省略記号なので、またいだ抜粋・含んだ抜粋は禁止
- fact_claims には、対比の成立に必要だが past/current の抜粋だけでは示せない事実を書く。
  発言一覧に根拠が無い事実は supporting_index / supporting_excerpt を null にする（後段で照合待ち扱い）
- score は言行不一致の明確さ（0-100）。70点以上のものだけ報告
- 該当がなければ空配列を返す
- JSON以外は出力しないでください"""


def _build_defense_prompt(speaker: str, card: PromiseCard, context_past: list[Speech], context_current: list[Speech], keyword: str | None) -> str:
	"""不履行・転向の理由を弁護するプロンプト"""

	def block(label: str, s: Speech, excerpt: str, context: list[Speech]) -> str:
		lines = [
			f"## {label}（{s.date} {s.name_of_house} {s.name_of_meeting} 出典:{s.source}）",
			f"発言者肩書: {s.speaker_position or '不明'} / 所属: {s.speaker_group or '不明'}",
			"",
			truncate_speech(s.speech_text, max_chars=2000, keyword=keyword),
		]
		if context:
			lines.append("")
			lines.append("### 前後の質疑（文脈）")
			for c in context:
				lines.append(f"[{c.speaker}] {truncate_speech(c.speech_text, max_chars=300)}")
		return "\n".join(lines)

	facts_text = "\n".join(f"- {f.claim}" for f in card.facts) or "（提示なし）"

	return f"""あなたは政治家「{speaker}」の弁護人です。
以下の「言行不一致（{card.card_type}）」という指摘に、反論できるかを全力で検討してください。

## 指摘内容
- 過去（{_speech_label(card.past_speech)}）: {card.past_summary}
- 現在（{_speech_label(card.current_speech)}）: {card.current_summary}
- 対比: {card.gap}
- 指摘側が挙げる中間事実:
{facts_text}

{block("過去の発言（約束/実績）", card.past_speech, card.past_excerpt, context_past)}

{block("現在の発言", card.current_speech, card.current_excerpt, context_current)}

## 検討すべき反論の観点
1. 権限の喪失: 約束後に政権交代・落選・役職離任などで実行する権限を失っていないか
2. 議会の制約: 法案の廃案・ねじれ国会・連立の制約など、本人の意思ではない外的要因はないか
3. 実は履行済み・履行中: 約束は（部分的にでも）実現していないか。現在も同じ目標を主張し続けていないか
4. 対象の違い: 過去の実績と現在の主張は、対象・範囲・期限が異なり両立しないか（例: 本体維持＋例外設定）
5. 状況の変化: 方針転換を正当化しうる重大な状況変化（経済情勢・災害・国際環境）はあるか
6. 誤読: そもそも「約束/実績」ではなく、願望・一般論・他者の発言ではないか

## 出力形式（JSONのみ）
```json
{{
  "verdict": "misread",
  "defense": "政治家側の最も説得力のある言い分（150文字以内）",
  "reason": "verdict の判定理由（100文字以内）"
}}
```

- verdict: "misread"=指摘自体が誤読（取り下げるべき） / "explainable"=事実だが相応の説明がつく / "confirmed"=反論を検討しても言行不一致という評価が妥当
- defense には confirmed の場合も「どの弁護を検討し、なぜ成立しなかったか」を書く
- 判定は厳格に。本人の意思による不履行・転向（優先順位の変更を含む）は安易に explainable としないこと。
  権限喪失や外的要因など、本人にはどうにもできなかった事情がある場合に explainable を選ぶこと
- JSON以外は出力しないでください"""


def extract_promise_cards(
	speaker: str,
	speeches: list[Speech],
	api_key: str | None = None,
	keyword: str | None = None,
) -> list[PromiseCard]:
	"""発言リストから約束トラッカーのカード候補を抽出する。"""
	if len(speeches) < 2:
		return []

	prompt = _build_extraction_prompt(speaker, speeches, keyword)
	print(f"  📝 推定入力トークン数: {_estimate_tokens(prompt):,}")

	data = generate_json(prompt, api_key=api_key)
	if not isinstance(data, list):
		raise RuntimeError(f"抽出レスポンスがリスト形式ではありません: {type(data)}")

	cards: list[PromiseCard] = []
	for item in data:
		card_type = str(item.get("card_type", "")).strip()
		idx_p = item.get("past_index", -1)
		idx_c = item.get("current_index", -1)
		if card_type not in CARD_TYPES:
			continue
		if not (0 <= idx_p < len(speeches) and 0 <= idx_c < len(speeches)):
			continue

		# 原文照合（インデックスずれは自動再アンカー）
		past_speech, past_excerpt, past_ok = _anchor_speech(
			"past", speeches, idx_p, item.get("past_excerpt", "")
		)
		current_speech, current_excerpt, current_ok = _anchor_speech(
			"current", speeches, idx_c, item.get("current_excerpt", "")
		)

		# 中間事実のアンカー照合
		facts: list[FactLine] = []
		for fc in item.get("fact_claims", []) or []:
			claim = str(fc.get("claim", "")).strip()
			if not claim:
				continue
			fact = FactLine(claim=claim)
			sup_idx = fc.get("supporting_index")
			sup_exc = fc.get("supporting_excerpt") or ""
			if sup_idx is not None and 0 <= int(sup_idx) < len(speeches) and sup_exc:
				anchor, exc, ok = _anchor_speech("fact", speeches, int(sup_idx), sup_exc)
				if ok:
					fact.anchor_speech = anchor
					fact.anchor_excerpt = exc
					fact.verified = True
			facts.append(fact)

		cards.append(
			PromiseCard(
				card_type=card_type,
				past_speech=past_speech,
				past_excerpt=past_excerpt,
				past_summary=item.get("past_summary", ""),
				deadline=str(item.get("deadline") or ""),
				current_speech=current_speech,
				current_excerpt=current_excerpt,
				current_summary=item.get("current_summary", ""),
				gap=item.get("gap", ""),
				score=int(item.get("score", 0)),
				facts=facts,
				past_verified=past_ok,
				current_verified=current_ok,
			)
		)

	# past は current より古いこと（逆なら不一致として棄却）
	cards = [
		c for c in cards
		if not (c.past_speech.date and c.current_speech.date)
		or c.past_speech.date < c.current_speech.date
	]

	cards.sort(key=lambda c: c.score, reverse=True)
	return cards


def verify_promise_cards(
	speaker: str,
	cards: list[PromiseCard],
	api_key: str | None = None,
	fetch_context: bool = True,
	keyword: str | None = None,
) -> list[PromiseCard]:
	"""カードを弁護人レビューにかけ、誤読を棄却して言い分を付ける。"""
	survived: list[PromiseCard] = []
	for i, card in enumerate(cards, 1):
		print(f"  ⚖️  弁護人レビュー {i}/{len(cards)} を実施中...")

		context_past: list[Speech] = []
		context_current: list[Speech] = []
		if fetch_context:
			context_past = fetch_context_speeches(card.past_speech)
			time.sleep(REQUEST_INTERVAL)
			context_current = fetch_context_speeches(card.current_speech)
			time.sleep(REQUEST_INTERVAL)

		prompt = _build_defense_prompt(speaker, card, context_past, context_current, keyword)
		try:
			data = generate_json(prompt, api_key=api_key)
		except Exception as e:
			print(f"    ⚠️ レビューに失敗したため未検証のまま保持します: {e}")
			survived.append(card)
			continue

		verdict = str(data.get("verdict", "")).strip() if isinstance(data, dict) else ""
		defense = str(data.get("defense", "")).strip() if isinstance(data, dict) else ""

		if verdict not in VALID_VERDICTS:
			print(f"    ⚠️ 不正な判定値（{verdict!r}）のため未検証のまま保持します")
			survived.append(card)
			continue
		if verdict == "misread":
			print(f"    ❌ 誤読と判定 → 棄却: {defense[:60]}")
			continue

		card.verdict = verdict
		card.defense = defense
		label = "🔥 言行不一致確定" if verdict == "confirmed" else "🛡️ 説明可能（言い分あり）"
		print(f"    {label}")
		survived.append(card)

	return survived
