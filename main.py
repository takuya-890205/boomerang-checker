#!/usr/bin/env python3
"""🪃 ブーメランチェッカー

国会議事録APIとClaude APIを使って、
議員の過去の矛盾・ブーメラン発言を検出するCLIツール。
"""

from __future__ import annotations

import argparse
import sys

from boomerang.analyzer import analyze_speeches
from boomerang.formatter import format_note, format_sns, format_terminal, format_x
from boomerang.kokkai_api import fetch_speeches


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		description="🪃 ブーメランチェッカー - 国会議員の矛盾発言を検出",
	)
	parser.add_argument(
		"speaker",
		nargs="?",
		help="議員名（例: 岸田文雄）",
	)
	parser.add_argument(
		"--max-speeches",
		type=int,
		default=100,
		help="取得する最大発言数（デフォルト: 100）",
	)
	parser.add_argument(
		"--from-date",
		help="取得開始日（YYYY-MM-DD形式）",
	)
	parser.add_argument(
		"--until-date",
		help="取得終了日（YYYY-MM-DD形式）",
	)
	parser.add_argument(
		"--api-key",
		help="Gemini API キー（省略時は環境変数 GEMINI_API_KEY を使用）",
	)
	parser.add_argument(
		"--sns",
		action="store_true",
		help="SNS投稿用の短縮フォーマットも表示",
	)
	parser.add_argument(
		"--x",
		action="store_true",
		help="X（旧Twitter）投稿用の140字フォーマットを表示",
	)
	parser.add_argument(
		"--note",
		help="Note向けマークダウンをファイル出力（ファイルパス省略時は {議員名}_boomerang.md に保存、'-' で標準出力）",
		nargs="?",
		const="",  # --note のみ指定時はデフォルトファイル名を使用
		metavar="OUTPUT_FILE",
	)
	return parser.parse_args()


def main() -> None:
	args = parse_args()

	# 議員名が引数で指定されなければ対話的に入力
	speaker = args.speaker
	if not speaker:
		speaker = input("議員名を入力してください: ").strip()
		if not speaker:
			print("エラー: 議員名を入力してください。", file=sys.stderr)
			sys.exit(1)

	# 1. 国会議事録APIから発言を取得
	print(f"\n⏳ 「{speaker}」の国会発言を取得中...")
	try:
		speeches = fetch_speeches(
			speaker_name=speaker,
			max_speeches=args.max_speeches,
			from_date=args.from_date,
			until_date=args.until_date,
			spread_years=True,
		)
	except Exception as e:
		print(f"エラー: 発言の取得に失敗しました: {e}", file=sys.stderr)
		sys.exit(1)

	if not speeches:
		print(f"「{speaker}」の発言が見つかりませんでした。")
		print("名前の表記を確認してください（例: 姓名の間にスペースなし）。")
		sys.exit(0)

	print(f"✅ {len(speeches)}件の発言を取得しました。")

	# 2. Gemini APIで矛盾分析
	print("🔍 Gemini APIで矛盾発言を分析中...")
	try:
		results = analyze_speeches(
			speaker=speaker,
			speeches=speeches,
			api_key=args.api_key,
		)
	except Exception as e:
		print(f"エラー: 分析に失敗しました: {e}", file=sys.stderr)
		print(
			"GEMINI_API_KEY 環境変数が設定されているか確認してください。",
			file=sys.stderr,
		)
		sys.exit(1)

	# 3. 結果表示
	print(format_terminal(speaker, results))

	# SNSフォーマット（--sns オプション）
	if args.sns:
		print("\n📋 SNS投稿用テキスト:")
		print("-" * 40)
		sns_text = format_sns(speaker, results)
		print(sns_text)
		print("-" * 40)
		print(f"（{len(sns_text)}文字）")

	# X（旧Twitter）フォーマット（--x オプション）
	if args.x:
		print("\n🐦 X（旧Twitter）投稿用テキスト:")
		print("-" * 40)
		x_text = format_x(speaker, results)
		print(x_text)
		print("-" * 40)

	# Note向けマークダウン（--note オプション）
	if args.note is not None:
		note_text = format_note(speaker, results)
		if args.note == "-":
			# 標準出力に表示
			print("\n📝 Note向けマークダウン:")
			print("=" * 40)
			print(note_text)
			print("=" * 40)
		else:
			# ファイルに出力（ファイルパス未指定時はデフォルト名を使用）
			output_path = args.note if args.note else f"{speaker}_boomerang.md"
			with open(output_path, "w", encoding="utf-8") as f:
				f.write(note_text)
			print(f"\n📝 Note向けマークダウンを保存しました: {output_path}")


if __name__ == "__main__":
	main()
