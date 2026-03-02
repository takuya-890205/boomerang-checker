"""ターミナル表示用フォーマッター

結果をターミナルに見やすく表示し、
note や X（旧Twitter）にそのまま貼れるテキスト形式も出力する。
"""

from __future__ import annotations

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
        lines.append("")
        lines.append(f"  📅 発言A（{year_a}年 {r.speech_a.name_of_meeting}）")
        lines.append(f"  「{r.summary_a}」")
        lines.append("")
        lines.append(f"  📅 発言B（{year_b}年 {r.speech_b.name_of_meeting}）")
        lines.append(f"  「{r.summary_b}」")
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
        lines.append("")

    lines.append(f"検出数: {len(results)}件")
    lines.append("")
    lines.append("#ブーメランチェッカー #国会 #議事録")

    return "\n".join(lines)
