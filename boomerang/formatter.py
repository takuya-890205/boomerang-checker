"""ターミナル表示用フォーマッター

結果をターミナルに見やすく表示し、
note や X（旧Twitter）にそのまま貼れるテキスト形式も出力する。
"""

from __future__ import annotations

from datetime import date

from boomerang.analyzer import BoomerangResult
from boomerang.promise_tracker import PromiseCard


def _score_bar(score: int) -> str:
	"""スコアをビジュアルバーで表現"""
	filled = score // 10
	empty = 10 - filled
	return f"[{'█' * filled}{'░' * empty}] {score}点"


def _score_emoji(score: int) -> str:
	"""スコアに応じた絵文字を返す"""
	if score >= 90:
		return "🔥"
	if score >= 80:
		return "🪃"
	return "⚠️"


def _truncate(text: str, max_chars: int) -> str:
	"""テキストを指定文字数で切り詰める（超過時は末尾に…を追加）"""
	if len(text) <= max_chars:
		return text
	return text[:max_chars - 1] + "…"


def _quote_a(r: BoomerangResult) -> tuple[str, bool]:
	"""発言Aの表示用引用文を返す（原文一致が検証済みなら原文抜粋を優先）。

	Returns:
		(引用文, 原文抜粋かどうか)
	"""
	if r.excerpt_a_verified and r.excerpt_a:
		return r.excerpt_a, True
	return r.summary_a, False


def _quote_b(r: BoomerangResult) -> tuple[str, bool]:
	"""発言Bの表示用引用文を返す（原文一致が検証済みなら原文抜粋を優先）"""
	if r.excerpt_b_verified and r.excerpt_b:
		return r.excerpt_b, True
	return r.summary_b, False


def _verdict_label(r: BoomerangResult) -> str:
	"""弁護人レビューの判定ラベルを返す（未実施は空文字）"""
	if r.verdict == "confirmed":
		return "🔥 弁護人レビュー通過（矛盾確定・断定調で公開可）"
	if r.verdict == "explainable":
		return "⚖️ グレー（説明可能・言い分併記でのみ公開可）"
	return ""


def _publishable_results(
	results: list[BoomerangResult], verified_run: bool
) -> list[BoomerangResult]:
	"""SNS公開用に結果を絞り込む。

	検証を実施した場合は原文一致済みの confirmed（断定調）と gray（言い分併記）のみ。
	confirmed を先頭に並べる（gray より公開優先度が高い）。
	"""
	if not verified_run:
		return results
	publishable = [r for r in results if r.publish_tier]
	publishable.sort(key=lambda r: (0 if r.publish_tier == "confirmed" else 1, -r.score))
	return publishable


def format_terminal(speaker: str, results: list[BoomerangResult]) -> str:
	"""ターミナル表示用のフォーマット"""
	lines: list[str] = []
	lines.append("")
	lines.append("=" * 56)
	lines.append(f"  🪃 ブーメランチェッカー  議員名：{speaker}")
	lines.append("=" * 56)

	if not results:
		lines.append("")
		lines.append("  矛盾する発言ペアは検出されませんでした。")
		lines.append("")
		lines.append("=" * 56)
		return "\n".join(lines)

	for i, r in enumerate(results, 1):
		year_a = r.speech_a.date[:4] if r.speech_a.date else "????"
		year_b = r.speech_b.date[:4] if r.speech_b.date else "????"
		emoji = _score_emoji(r.score)
		quote_a, is_original_a = _quote_a(r)
		quote_b, is_original_b = _quote_b(r)

		lines.append("")
		lines.append(f"  {emoji} ブーメラン #{i}")
		lines.append(f"  矛盾スコア: {_score_bar(r.score)}")
		# 年の差がある場合は表示
		if r.year_gap > 0:
			lines.append(f"  ⏱ 年の差: {r.year_gap}年")
		verdict_label = _verdict_label(r)
		if verdict_label:
			lines.append(f"  {verdict_label}")
		lines.append("")
		lines.append(f"  📅 発言A（{year_a}年 {r.speech_a.name_of_meeting}）［出典: {r.speech_a.source} {r.speech_a.source_grade}］")
		lines.append(f"  「{quote_a}」{'（原文）' if is_original_a else '（AI要約）'}")
		# 発言AのURLを表示
		if r.speech_a.speech_url:
			lines.append(f"  🔗 {r.speech_a.speech_url}")
		lines.append("")
		lines.append(f"  📅 発言B（{year_b}年 {r.speech_b.name_of_meeting}）［出典: {r.speech_b.source} {r.speech_b.source_grade}］")
		lines.append(f"  「{quote_b}」{'（原文）' if is_original_b else '（AI要約）'}")
		# 発言BのURLを表示
		if r.speech_b.speech_url:
			lines.append(f"  🔗 {r.speech_b.speech_url}")
		lines.append("")
		lines.append(f"  💬 {r.contradiction}")
		# 弁護人レビューの言い分（両論併記）
		if r.defense:
			lines.append(f"  ⚖️ 言い分: {r.defense}")
		lines.append("")
		lines.append("-" * 56)

	lines.append("")
	avg_score = sum(r.score for r in results) / len(results)
	lines.append(f"  検出数: {len(results)}件  平均矛盾スコア: {avg_score:.0f}点")
	lines.append("")
	lines.append("=" * 56)

	return "\n".join(lines)


def format_sns(
	speaker: str,
	results: list[BoomerangResult],
	verified_run: bool = False,
) -> str:
	"""SNS（note / X）投稿用の短縮フォーマット。

	弁護人レビューを実施した場合（verified_run=True）は、レビューを通過し
	原文一致も確認できたペア（is_publishable）だけを対象にする。
	"""
	publishable = _publishable_results(results, verified_run)

	lines: list[str] = []
	lines.append(f"🪃 ブーメランチェッカー：{speaker}")
	lines.append("")

	if not publishable:
		if verified_run and results:
			lines.append("検出された候補はいずれも検証（文脈・原文照合）を通過しませんでした。")
		else:
			lines.append("矛盾する発言は検出されませんでした。")
		return "\n".join(lines)

	for i, r in enumerate(publishable, 1):
		year_a = r.speech_a.date[:4] if r.speech_a.date else "????"
		year_b = r.speech_b.date[:4] if r.speech_b.date else "????"
		quote_a, _ = _quote_a(r)
		quote_b, _ = _quote_b(r)

		if verified_run and r.publish_tier == "gray":
			# グレー枠: 断定せず、事実の対比＋想定される言い分の両論併記で出す
			lines.append(f"⚖️ 立場の変化 #{i}（矛盾スコア: {r.score}点・言い分あり）")
			lines.append(f"発言A（{year_a}年）：「{quote_a}」")
			lines.append(f"発言B（{year_b}年）：「{quote_b}」")
			lines.append(f"→ {r.contradiction}")
			if r.defense:
				lines.append(f"⚖️ 想定される言い分: {r.defense}")
			lines.append("判定はあなたに委ねます。")
		else:
			emoji = _score_emoji(r.score)
			lines.append(f"{emoji} ブーメラン #{i}（矛盾スコア: {r.score}点）")
			lines.append(f"発言A（{year_a}年）：「{quote_a}」")
			lines.append(f"発言B（{year_b}年）：「{quote_b}」")
			lines.append(f"→ {r.contradiction}")
		# 発言AのURLを表示
		if r.speech_a.speech_url:
			lines.append(f"🔗 {r.speech_a.speech_url}")
		lines.append("")

	lines.append(f"検出数: {len(publishable)}件")
	if verified_run:
		lines.append("※引用は一次ソースの原文・前後文脈まで検証済み（出典リンクから全文を確認できます）")
	lines.append("")
	lines.append("#ブーメランチェッカー #国会 #議事録")

	return "\n".join(lines)


def format_x(
	speaker: str,
	results: list[BoomerangResult],
	verified_run: bool = False,
) -> str:
	"""X（旧Twitter）投稿用140字フォーマット。
	公開可能な結果のうち最もスコアが高い1件のみを対象とする。
	"""
	publishable = _publishable_results(results, verified_run)

	if not publishable:
		if verified_run and results:
			return (
				f"🪃【ブーメラン】{speaker}\n\n"
				"検出された候補はいずれも検証（文脈・原文照合）を通過しませんでした。"
			)
		return f"🪃【ブーメラン】{speaker}\n\n矛盾する発言は検出されませんでした。"

	# 公開優先度順（confirmed→gray、同順位はスコア降順）の先頭1件を使用
	r = publishable[0]
	year_a = r.speech_a.date[:4] if r.speech_a.date else "????"
	year_b = r.speech_b.date[:4] if r.speech_b.date else "????"
	is_gray = verified_run and r.publish_tier == "gray"

	# 各フィールドを切り詰め（原文抜粋が検証済みならそちらを使う）
	quote_a, _ = _quote_a(r)
	quote_b, _ = _quote_b(r)
	quote_a = _truncate(quote_a, 40)
	quote_b = _truncate(quote_b, 40)
	contradiction = _truncate(r.contradiction, 40)

	lines: list[str] = []
	if is_gray:
		lines.append(f"🪃【ブーメラン？】{speaker}")
	else:
		lines.append(f"🪃【ブーメラン】{speaker}")
	lines.append("")
	lines.append(f"❌{year_a}年「{quote_a}」")
	lines.append("")
	lines.append(f"✅{year_b}年「{quote_b}」")
	lines.append("")
	lines.append(f"→ {contradiction}")
	if is_gray:
		# グレー枠は断定せず、想定される言い分を併記して判定を読者に委ねる
		lines.append(f"⚖️ 想定される言い分: {_truncate(r.defense, 60)}")
		lines.append("")
		lines.append("あなたはどう見ますか？")
	else:
		lines.append("")
		lines.append(f"矛盾スコア:{r.score}点")
	if verified_run:
		lines.append("※発言は原文より引用（リンク先で全文確認可）")
	lines.append(f"#{speaker} #ブーメランチェッカー #国会議事録")
	# 発言AのURLを追加
	if r.speech_a.speech_url:
		lines.append("")
		lines.append(f"🔗 {r.speech_a.speech_url}")

	text = "\n".join(lines)
	char_count = len(text)

	# 文字数を末尾に付加
	return text + f"\n（{char_count}字）"


def format_note(speaker: str, results: list[BoomerangResult]) -> str:
	"""Note向けマークダウンフォーマット。"""
	today_str = date.today().strftime("%Y年%m月%d日")

	lines: list[str] = []
	lines.append(f"# 🪃 {speaker} ブーメラン発言まとめ（{today_str}時点）")
	lines.append("")
	lines.append("> 国会議事録API等の一次ソースとGemini AIを使って自動検出した矛盾発言です。")
	lines.append("> 主データソース：[国立国会図書館 国会会議録検索システム](https://kokkai.ndl.go.jp/)")
	lines.append("")
	lines.append("---")

	if not results:
		lines.append("")
		lines.append("矛盾する発言は検出されませんでした。")
		lines.append("")
		lines.append("---")
		lines.append("")
		lines.append("#ブーメランチェッカー #国会議事録")
		return "\n".join(lines)

	for i, r in enumerate(results, 1):
		year_a = r.speech_a.date[:4] if r.speech_a.date else "????"
		year_b = r.speech_b.date[:4] if r.speech_b.date else "????"
		quote_a, is_original_a = _quote_a(r)
		quote_b, is_original_b = _quote_b(r)

		lines.append("")
		lines.append(f"## 🔥 #{i} 矛盾スコア：{r.score}点")
		# 年の差がある場合は表示
		if r.year_gap > 0:
			lines.append(f"（発言の年の差：{r.year_gap}年）")
		verdict_label = _verdict_label(r)
		if verdict_label:
			lines.append(f"（{verdict_label}）")
		lines.append("")
		lines.append(f"### 発言A（{year_a}年 {r.speech_a.name_of_meeting}）")
		lines.append(f"> {quote_a}")
		lines.append("")
		if is_original_a:
			lines.append(f"（{r.speech_a.source}原文より引用・出典グレード{r.speech_a.source_grade}）")
			lines.append("")
		# 発言AのURL
		if r.speech_a.speech_url:
			lines.append(f"[🔗 国会議事録を確認する]({r.speech_a.speech_url})")
			lines.append("")
		lines.append(f"### 発言B（{year_b}年 {r.speech_b.name_of_meeting}）")
		lines.append(f"> {quote_b}")
		lines.append("")
		if is_original_b:
			lines.append(f"（{r.speech_b.source}原文より引用・出典グレード{r.speech_b.source_grade}）")
			lines.append("")
		# 発言BのURL
		if r.speech_b.speech_url:
			lines.append(f"[🔗 国会議事録を確認する]({r.speech_b.speech_url})")
			lines.append("")
		lines.append(f"**矛盾点：** {r.contradiction}")
		# 弁護人レビューの言い分（両論併記で切り抜き批判に備える）
		if r.defense:
			lines.append("")
			lines.append(f"**⚖️ 想定される言い分：** {r.defense}")
		lines.append("")
		lines.append("---")

	lines.append("")
	lines.append("#ブーメランチェッカー #国会議事録")

	return "\n".join(lines)


# ============================================================
# 約束トラッカー（PromiseCard）用フォーマッター
# ============================================================

_TYPE_ICON = {"約束不履行型": "📜", "実績否認型": "🏛️"}


def _card_quote(excerpt: str, verified: bool, summary: str) -> str:
	"""カードの引用表示（原文照合済みなら原文、そうでなければAI要約にフォールバック）"""
	if verified and excerpt:
		return f"「{excerpt}」（原文）"
	return f"「{summary}」（AI要約）"


def _card_venue(s) -> str:
	year = s.date[:4] if s.date else "????"
	venue = f"{s.name_of_house} {s.name_of_meeting}".strip()
	return f"{year}年 {venue}"


def _card_verdict_label(card: PromiseCard) -> str:
	if card.verdict == "confirmed":
		return "🔥 弁護人レビュー通過（言行不一致確定・断定調で公開可）"
	if card.verdict == "explainable":
		return "⚖️ グレー（説明可能・言い分併記でのみ公開可）"
	return ""


def format_promise_terminal(speaker: str, cards: list[PromiseCard]) -> str:
	"""約束トラッカーのターミナル表示"""
	lines: list[str] = []
	lines.append("")
	lines.append("=" * 56)
	lines.append(f"  🪃 約束トラッカー  議員名：{speaker}")
	lines.append("=" * 56)

	if not cards:
		lines.append("")
		lines.append("  言行不一致のカードは検出されませんでした。")
		lines.append("")
		lines.append("=" * 56)
		return "\n".join(lines)

	for i, c in enumerate(cards, 1):
		icon = _TYPE_ICON.get(c.card_type, "🪃")
		lines.append("")
		lines.append(f"  {icon} カード#{i}【{c.card_type}】 スコア: {_score_bar(c.score)}")
		verdict_label = _card_verdict_label(c)
		if verdict_label:
			lines.append(f"  {verdict_label}")
		lines.append("")
		past_label = "約束" if c.card_type == "約束不履行型" else "実績"
		lines.append(f"  {icon} {past_label}（{_card_venue(c.past_speech)}）［出典: {c.past_speech.source} {c.past_speech.source_grade}］")
		lines.append(f"  {_card_quote(c.past_excerpt, c.past_verified, c.past_summary)}")
		if c.deadline:
			lines.append(f"  ⏰ 期限の言明: {c.deadline}")
		if c.past_speech.speech_url:
			lines.append(f"  🔗 {c.past_speech.speech_url}")
		if c.facts:
			lines.append("")
			lines.append("  📉 事実経過:")
			for f in c.facts:
				if f.verified:
					lines.append(f"    ✅ {f.claim}")
					anchor = f.anchor_speech
					if anchor is not None:
						lines.append(f"       └ 根拠: {_card_venue(anchor)}「{f.anchor_excerpt[:60]}」")
						if anchor.speech_url:
							lines.append(f"         🔗 {anchor.speech_url}")
				else:
					lines.append(f"    ⚠️ {f.claim}（発言中に根拠なし・機械照合待ち。SNS出力からは除外）")
		lines.append("")
		lines.append(f"  🔄 現在（{_card_venue(c.current_speech)}）［出典: {c.current_speech.source} {c.current_speech.source_grade}］")
		lines.append(f"  {_card_quote(c.current_excerpt, c.current_verified, c.current_summary)}")
		if c.current_speech.speech_url:
			lines.append(f"  🔗 {c.current_speech.speech_url}")
		lines.append("")
		lines.append(f"  💬 {c.gap}")
		if c.defense:
			lines.append(f"  ⚖️ 想定される言い分: {c.defense}")
		lines.append("")
		lines.append("-" * 56)

	lines.append("")
	lines.append(f"  検出数: {len(cards)}件")
	lines.append("")
	lines.append("=" * 56)
	return "\n".join(lines)


def _publishable_cards(cards: list[PromiseCard], verified_run: bool) -> list[PromiseCard]:
	if not verified_run:
		return cards
	publishable = [c for c in cards if c.publish_tier]
	publishable.sort(key=lambda c: (0 if c.publish_tier == "confirmed" else 1, -c.score))
	return publishable


def _format_promise_card_sns(c: PromiseCard, verified_run: bool) -> list[str]:
	"""SNS向けのカード1枚分のテキスト行"""
	icon = _TYPE_ICON.get(c.card_type, "🪃")
	past_year = c.past_speech.date[:4] if c.past_speech.date else "????"
	cur_year = c.current_speech.date[:4] if c.current_speech.date else "????"
	past_label = "約束" if c.card_type == "約束不履行型" else "実績"

	lines: list[str] = []
	lines.append(f"{icon} {past_year}年（{past_label}）「{c.past_excerpt}」")
	for f in c.verified_facts:
		lines.append(f"📉 {f.claim}")
	lines.append(f"🔄 {cur_year}年「{c.current_excerpt}」")
	lines.append(f"→ {c.gap}")
	if verified_run and c.publish_tier == "gray":
		lines.append(f"⚖️ 想定される言い分: {c.defense}")
		lines.append("判定はあなたに委ねます。")
	elif verified_run and c.publish_tier == "confirmed":
		lines.append("⚖️ 弁護人レビューでも説明がつかず、言行不一致が確定")
	if c.past_speech.speech_url:
		lines.append(f"🔗 {c.past_speech.speech_url}")
	if c.current_speech.speech_url:
		lines.append(f"🔗 {c.current_speech.speech_url}")
	return lines


def format_promise_sns(speaker: str, cards: list[PromiseCard], verified_run: bool = False) -> str:
	"""約束トラッカーのSNS投稿用フォーマット"""
	publishable = _publishable_cards(cards, verified_run)

	lines: list[str] = []
	lines.append(f"🪃 約束トラッカー：{speaker}")
	lines.append("")

	if not publishable:
		if verified_run and cards:
			lines.append("検出された候補はいずれも検証（原文照合・弁護人レビュー）を通過しませんでした。")
		else:
			lines.append("言行不一致は検出されませんでした。")
		return "\n".join(lines)

	for i, c in enumerate(publishable, 1):
		lines.append(f"【{c.card_type} #{i}】")
		lines.extend(_format_promise_card_sns(c, verified_run))
		lines.append("")

	lines.append(f"検出数: {len(publishable)}件")
	if verified_run:
		lines.append("※引用は一次ソースの原文と照合済み（出典リンクから全文を確認できます）")
	lines.append("")
	lines.append("#約束トラッカー #国会 #議事録")
	return "\n".join(lines)


def format_promise_x(speaker: str, cards: list[PromiseCard], verified_run: bool = False) -> str:
	"""約束トラッカーのX投稿用（公開優先度が最も高い1枚）"""
	publishable = _publishable_cards(cards, verified_run)
	if not publishable:
		return (
			f"🪃【約束トラッカー】{speaker}\n\n"
			"検出された候補はいずれも検証（原文照合・弁護人レビュー）を通過しませんでした。"
		)

	c = publishable[0]
	is_gray = verified_run and c.publish_tier == "gray"
	icon = _TYPE_ICON.get(c.card_type, "🪃")
	past_year = c.past_speech.date[:4] if c.past_speech.date else "????"
	cur_year = c.current_speech.date[:4] if c.current_speech.date else "????"

	lines: list[str] = []
	title = "約束トラッカー？" if is_gray else "約束トラッカー"
	lines.append(f"🪃【{title}】{speaker}（{c.card_type}）")
	lines.append("")
	lines.append(f"{icon}{past_year}年「{_truncate(c.past_excerpt, 60)}」")
	for f in c.verified_facts[:2]:
		lines.append(f"📉 {_truncate(f.claim, 40)}")
	lines.append(f"🔄{cur_year}年「{_truncate(c.current_excerpt, 60)}」")
	lines.append("")
	lines.append(f"→ {_truncate(c.gap, 60)}")
	if is_gray:
		lines.append(f"⚖️ 想定される言い分: {_truncate(c.defense, 60)}")
		lines.append("")
		lines.append("あなたはどう見ますか？")
	if verified_run:
		lines.append("※発言は原文より引用（リンク先で全文確認可）")
	lines.append(f"#{speaker} #約束トラッカー")
	if c.past_speech.speech_url:
		lines.append("")
		lines.append(f"🔗 {c.past_speech.speech_url}")

	text = "\n".join(lines)
	return text + f"\n（{len(text)}字）"


def format_promise_note(speaker: str, cards: list[PromiseCard]) -> str:
	"""約束トラッカーのNote向けマークダウン"""
	today_str = date.today().strftime("%Y年%m月%d日")
	lines: list[str] = []
	lines.append(f"# 🪃 {speaker} 約束トラッカー（{today_str}時点）")
	lines.append("")
	lines.append("> 「言ったこと」と「やったこと」の突き合わせを、国会議事録・本人Xポスト等の一次ソース原文で行った結果です。")
	lines.append("> 引用はすべて原文との機械照合済み。想定される言い分も併記しています。")
	lines.append("")
	lines.append("---")

	if not cards:
		lines.append("")
		lines.append("言行不一致は検出されませんでした。")
		return "\n".join(lines)

	for i, c in enumerate(cards, 1):
		past_label = "📜 約束" if c.card_type == "約束不履行型" else "🏛️ 実績"
		lines.append("")
		lines.append(f"## #{i}【{c.card_type}】スコア: {c.score}点")
		verdict_label = _card_verdict_label(c)
		if verdict_label:
			lines.append(f"（{verdict_label}）")
		lines.append("")
		lines.append(f"### {past_label}（{_card_venue(c.past_speech)}）")
		lines.append(f"> {c.past_excerpt if c.past_verified else c.past_summary}")
		lines.append("")
		if c.deadline:
			lines.append(f"⏰ 期限の言明: {c.deadline}")
			lines.append("")
		if c.past_speech.speech_url:
			lines.append(f"[🔗 原文を確認する]({c.past_speech.speech_url})")
			lines.append("")
		if c.verified_facts:
			lines.append("### 📉 事実経過")
			for f in c.verified_facts:
				anchor = f.anchor_speech
				if anchor is not None and anchor.speech_url:
					lines.append(f"- {f.claim}（[根拠: {_card_venue(anchor)}]({anchor.speech_url})）")
				else:
					lines.append(f"- {f.claim}")
			lines.append("")
		lines.append(f"### 🔄 現在（{_card_venue(c.current_speech)}）")
		lines.append(f"> {c.current_excerpt if c.current_verified else c.current_summary}")
		lines.append("")
		if c.current_speech.speech_url:
			lines.append(f"[🔗 原文を確認する]({c.current_speech.speech_url})")
			lines.append("")
		lines.append(f"**対比:** {c.gap}")
		if c.defense:
			lines.append("")
			lines.append(f"**⚖️ 想定される言い分:** {c.defense}")
		lines.append("")
		lines.append("---")

	lines.append("")
	lines.append("#約束トラッカー #国会議事録")
	return "\n".join(lines)
