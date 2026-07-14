"""ターミナル表示用フォーマッター

結果をターミナルに見やすく表示し、
note や X（旧Twitter）にそのまま貼れるテキスト形式も出力する。
"""

from __future__ import annotations

from datetime import date

from boomerang.analyzer import BoomerangResult


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
		return "🔥 弁護人レビュー通過（矛盾確定）"
	if r.verdict == "explainable":
		return "🛡️ 説明可能（言い分あり・SNS公開非推奨）"
	return ""


def _publishable_results(
	results: list[BoomerangResult], verified_run: bool
) -> list[BoomerangResult]:
	"""SNS公開用に結果を絞り込む。検証を実施した場合は confirmed ＋原文一致のみ"""
	if not verified_run:
		return results
	return [r for r in results if r.is_publishable]


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
		lines.append(f"  📅 発言A（{year_a}年 {r.speech_a.name_of_meeting}）")
		lines.append(f"  「{quote_a}」{'（原文）' if is_original_a else '（AI要約）'}")
		# 発言AのURLを表示
		if r.speech_a.speech_url:
			lines.append(f"  🔗 {r.speech_a.speech_url}")
		lines.append("")
		lines.append(f"  📅 発言B（{year_b}年 {r.speech_b.name_of_meeting}）")
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
		emoji = _score_emoji(r.score)
		quote_a, _ = _quote_a(r)
		quote_b, _ = _quote_b(r)

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
		lines.append("※引用は議事録原文・前後文脈まで検証済み（出典リンクから全文を確認できます）")
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

	# スコア最高の1件を使用
	r = publishable[0]
	year_a = r.speech_a.date[:4] if r.speech_a.date else "????"
	year_b = r.speech_b.date[:4] if r.speech_b.date else "????"

	# 各フィールドを切り詰め（原文抜粋が検証済みならそちらを使う）
	quote_a, _ = _quote_a(r)
	quote_b, _ = _quote_b(r)
	quote_a = _truncate(quote_a, 40)
	quote_b = _truncate(quote_b, 40)
	contradiction = _truncate(r.contradiction, 40)

	lines: list[str] = []
	lines.append(f"🪃【ブーメラン】{speaker}")
	lines.append("")
	lines.append(f"❌{year_a}年「{quote_a}」")
	lines.append("")
	lines.append(f"✅{year_b}年「{quote_b}」")
	lines.append("")
	lines.append(f"→ {contradiction}")
	lines.append("")
	lines.append(f"矛盾スコア:{r.score}点")
	if verified_run:
		lines.append("※議事録原文より引用（リンク先で全文確認可）")
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
	lines.append("> 国会議事録APIとGemini AIを使って自動検出した矛盾発言です。")
	lines.append("> データソース：[国立国会図書館 国会会議録検索システム](https://kokkai.ndl.go.jp/)")
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
			lines.append("（議事録原文より引用）")
			lines.append("")
		# 発言AのURL
		if r.speech_a.speech_url:
			lines.append(f"[🔗 国会議事録を確認する]({r.speech_a.speech_url})")
			lines.append("")
		lines.append(f"### 発言B（{year_b}年 {r.speech_b.name_of_meeting}）")
		lines.append(f"> {quote_b}")
		lines.append("")
		if is_original_b:
			lines.append("（議事録原文より引用）")
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
