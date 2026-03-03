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

		lines.append("")
		lines.append(f"  {emoji} ブーメラン #{i}")
		lines.append(f"  矛盾スコア: {_score_bar(r.score)}")
		# 年の差がある場合は表示
		if r.year_gap > 0:
			lines.append(f"  ⏱ 年の差: {r.year_gap}年")
		lines.append("")
		lines.append(f"  📅 発言A（{year_a}年 {r.speech_a.name_of_meeting}）")
		lines.append(f"  「{r.summary_a}」")
		# 発言AのURLを表示
		if r.speech_a.speech_url:
			lines.append(f"  🔗 {r.speech_a.speech_url}")
		lines.append("")
		lines.append(f"  📅 発言B（{year_b}年 {r.speech_b.name_of_meeting}）")
		lines.append(f"  「{r.summary_b}」")
		# 発言BのURLを表示
		if r.speech_b.speech_url:
			lines.append(f"  🔗 {r.speech_b.speech_url}")
		lines.append("")
		lines.append(f"  💬 {r.contradiction}")
		lines.append("")
		lines.append("-" * 56)

	lines.append("")
	avg_score = sum(r.score for r in results) / len(results)
	lines.append(f"  検出数: {len(results)}件  平均矛盾スコア: {avg_score:.0f}点")
	lines.append("")
	lines.append("=" * 56)

	return "\n".join(lines)


def format_sns(speaker: str, results: list[BoomerangResult]) -> str:
	"""SNS（note / X）投稿用の短縮フォーマット"""
	lines: list[str] = []
	lines.append(f"🪃 ブーメランチェッカー：{speaker}")
	lines.append("")

	if not results:
		lines.append("矛盾する発言は検出されませんでした。")
		return "\n".join(lines)

	for i, r in enumerate(results, 1):
		year_a = r.speech_a.date[:4] if r.speech_a.date else "????"
		year_b = r.speech_b.date[:4] if r.speech_b.date else "????"
		emoji = _score_emoji(r.score)

		lines.append(f"{emoji} ブーメラン #{i}（矛盾スコア: {r.score}点）")
		lines.append(f"発言A（{year_a}年）：「{r.summary_a}」")
		lines.append(f"発言B（{year_b}年）：「{r.summary_b}」")
		lines.append(f"→ {r.contradiction}")
		# 発言AのURLを表示
		if r.speech_a.speech_url:
			lines.append(f"🔗 {r.speech_a.speech_url}")
		lines.append("")

	lines.append(f"検出数: {len(results)}件")
	lines.append("")
	lines.append("#ブーメランチェッカー #国会 #議事録")

	return "\n".join(lines)


def format_x(speaker: str, results: list[BoomerangResult]) -> str:
	"""X（旧Twitter）投稿用140字フォーマット。
	最もスコアが高い1件のみを対象とする。
	"""
	if not results:
		return f"🪃【ブーメラン】{speaker}\n\n矛盾する発言は検出されませんでした。"

	# スコア最高の1件を使用
	r = results[0]
	year_a = r.speech_a.date[:4] if r.speech_a.date else "????"
	year_b = r.speech_b.date[:4] if r.speech_b.date else "????"

	# 各フィールドを切り詰め
	summary_a = _truncate(r.summary_a, 30)
	summary_b = _truncate(r.summary_b, 30)
	contradiction = _truncate(r.contradiction, 40)

	lines: list[str] = []
	lines.append(f"🪃【ブーメラン】{speaker}")
	lines.append("")
	lines.append(f"❌{year_a}年「{summary_a}」")
	lines.append("")
	lines.append(f"✅{year_b}年「{summary_b}」")
	lines.append("")
	lines.append(f"→ {contradiction}")
	lines.append("")
	lines.append(f"矛盾スコア:{r.score}点")
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

		lines.append("")
		lines.append(f"## 🔥 #{i} 矛盾スコア：{r.score}点")
		# 年の差がある場合は表示
		if r.year_gap > 0:
			lines.append(f"（発言の年の差：{r.year_gap}年）")
		lines.append("")
		lines.append(f"### 発言A（{year_a}年 {r.speech_a.name_of_meeting}）")
		lines.append(f"> {r.summary_a}")
		lines.append("")
		# 発言AのURL
		if r.speech_a.speech_url:
			lines.append(f"[🔗 国会議事録を確認する]({r.speech_a.speech_url})")
			lines.append("")
		lines.append(f"### 発言B（{year_b}年 {r.speech_b.name_of_meeting}）")
		lines.append(f"> {r.summary_b}")
		lines.append("")
		# 発言BのURL
		if r.speech_b.speech_url:
			lines.append(f"[🔗 国会議事録を確認する]({r.speech_b.speech_url})")
			lines.append("")
		lines.append(f"**矛盾点：** {r.contradiction}")
		lines.append("")
		lines.append("---")

	lines.append("")
	lines.append("#ブーメランチェッカー #国会議事録")

	return "\n".join(lines)
