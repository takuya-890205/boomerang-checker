#!/usr/bin/env python3
"""🪃 ブーメランチェッカー

国会議事録APIとGemini APIを使って、
議員の過去の矛盾・ブーメラン発言を検出するCLIツール。
検出結果は弁護人レビュー（前後文脈・原文照合による検証）を通してから出力する。
"""

from __future__ import annotations

import argparse
import sys

from boomerang.analyzer import analyze_speeches, set_llm_provider
from boomerang.formatter import (
	format_note,
	format_promise_note,
	format_promise_sns,
	format_promise_terminal,
	format_promise_x,
	format_sns,
	format_terminal,
	format_x,
)
from boomerang.kokkai_api import fetch_speeches
from boomerang.promise_tracker import extract_promise_cards, verify_promise_cards
from boomerang.verifier import verify_results
from boomerang.web_source import fetch_web_source, parse_source_url_arg
from boomerang.x_posts import fetch_x_posts


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
		"--keyword",
		help="争点キーワード（例: 定数削減）。発言本文で絞り込み、10〜30年前まで遡って照合する",
	)
	parser.add_argument(
		"--x-handle",
		help="本人のXアカウント名（例: @xxxx）。指定すると本人ポストも照合対象に加える（一次ソース・グレードA）",
	)
	parser.add_argument(
		"--source-url",
		action="append",
		default=[],
		metavar="[YYYY-MM-DD:]URL",
		help="公式サイトの全文ページ（会見録・党大会演説等）を照合対象に加える（グレードB）。複数指定可。日付をページから特定できない場合は 'YYYY-MM-DD:URL' 形式で指定",
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
		"--llm",
		choices=["gemini", "claude"],
		default="gemini",
		help="分析に使うLLM。claude は Claude Code CLI のサブスク枠を使用（APIキー不要・高精度・低速）",
	)
	parser.add_argument(
		"--promise",
		action="store_true",
		help="約束トラッカーモード（言vs行）。過去の約束・実績と現在の態度を突き合わせる（発言vs発言の矛盾検出の代わりに実行）",
	)
	parser.add_argument(
		"--no-verify",
		action="store_true",
		help="弁護人レビュー（文脈検証）をスキップする（API使用量の節約用。SNS出力の信頼性は下がる）",
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

	# LLMプロバイダの設定
	set_llm_provider(args.llm)
	if args.llm == "claude":
		print("🤖 LLM: Claude（Claude Code CLI・サブスク枠）")

	# 議員名が引数で指定されなければ対話的に入力
	speaker = args.speaker
	if not speaker:
		speaker = input("議員名を入力してください: ").strip()
		if not speaker:
			print("エラー: 議員名を入力してください。", file=sys.stderr)
			sys.exit(1)

	# 1. 国会議事録APIから発言を取得
	if args.keyword:
		print(f"\n⏳ 「{speaker}」の国会発言（争点: {args.keyword}）を取得中...")
	else:
		print(f"\n⏳ 「{speaker}」の国会発言を取得中...")
	try:
		speeches = fetch_speeches(
			speaker_name=speaker,
			max_speeches=args.max_speeches,
			from_date=args.from_date,
			until_date=args.until_date,
			spread_years=True,
			keyword=args.keyword,
		)
	except Exception as e:
		print(f"エラー: 発言の取得に失敗しました: {e}", file=sys.stderr)
		sys.exit(1)

	if not speeches:
		print(f"「{speaker}」の発言が見つかりませんでした。")
		print("名前の表記を確認してください（例: 姓名の間にスペースなし）。")
		sys.exit(0)

	print(f"✅ {len(speeches)}件の発言を取得しました。")

	# 1b. 本人Xポスト（--x-handle オプション）
	if args.x_handle:
		print(f"🐦 本人Xポスト（{args.x_handle}）を取得中...")
		x_speeches = fetch_x_posts(
			speaker_name=speaker,
			handle=args.x_handle,
			keyword=args.keyword,
		)
		if x_speeches:
			print(f"✅ 本人Xポスト {len(x_speeches)}件を照合対象に追加しました。")
			speeches.extend(x_speeches)

	# 1c. 公式サイトの全文ページ（--source-url オプション）
	for raw_arg in args.source_url:
		url, url_date = parse_source_url_arg(raw_arg)
		print(f"🌐 全文ページを取得中: {url}")
		web_speech = fetch_web_source(speaker_name=speaker, url=url, date=url_date)
		if web_speech:
			label = web_speech.date or "日付不明"
			print(f"✅ 全文ページ（{label}・{len(web_speech.speech_text):,}字）を照合対象に追加しました。")
			speeches.append(web_speech)

	# 約束トラッカーモード（言vs行）
	if args.promise:
		run_promise_mode(args, speaker, speeches)
		return

	# 2. Gemini APIで矛盾分析
	print("🔍 Gemini APIで矛盾発言を分析中...")
	try:
		results = analyze_speeches(
			speaker=speaker,
			speeches=speeches,
			api_key=args.api_key,
			keyword=args.keyword,
		)
	except Exception as e:
		print(f"エラー: 分析に失敗しました: {e}", file=sys.stderr)
		print(
			"GEMINI_API_KEY 環境変数が設定されているか確認してください。",
			file=sys.stderr,
		)
		sys.exit(1)

	# 3. 弁護人レビュー（文脈検証・切り抜き防止）
	verified_run = False
	if results and not args.no_verify:
		print(f"⚖️  検出された{len(results)}組を弁護人レビューで検証中...")
		try:
			before = len(results)
			results = verify_results(
				speaker=speaker,
				results=results,
				api_key=args.api_key,
				keyword=args.keyword,
			)
			verified_run = True
			dropped = before - len(results)
			if dropped:
				print(f"✅ 検証完了: {dropped}組を誤読として棄却し、{len(results)}組が残りました。")
			else:
				print(f"✅ 検証完了: {len(results)}組すべてが検証を通過しました。")
		except Exception as e:
			print(f"⚠️ 弁護人レビューに失敗したため未検証の結果を表示します: {e}", file=sys.stderr)

	# 4. 結果表示
	print(format_terminal(speaker, results))

	# SNSフォーマット（--sns オプション）
	if args.sns:
		print("\n📋 SNS投稿用テキスト:")
		print("-" * 40)
		sns_text = format_sns(speaker, results, verified_run=verified_run)
		print(sns_text)
		print("-" * 40)
		print(f"（{len(sns_text)}文字）")

	# X（旧Twitter）フォーマット（--x オプション）
	if args.x:
		print("\n🐦 X（旧Twitter）投稿用テキスト:")
		print("-" * 40)
		x_text = format_x(speaker, results, verified_run=verified_run)
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


def run_promise_mode(args: argparse.Namespace, speaker: str, speeches: list) -> None:
	"""約束トラッカーモードの実行（抽出→弁護人レビュー→カード出力）"""
	print("🔍 約束・実績と現在の態度を突き合わせ中...")
	try:
		cards = extract_promise_cards(
			speaker=speaker,
			speeches=speeches,
			api_key=args.api_key,
			keyword=args.keyword,
		)
	except Exception as e:
		print(f"エラー: 抽出に失敗しました: {e}", file=sys.stderr)
		sys.exit(1)

	verified_run = False
	if cards and not args.no_verify:
		print(f"⚖️  検出された{len(cards)}枚を弁護人レビューで検証中...")
		try:
			before = len(cards)
			cards = verify_promise_cards(
				speaker=speaker,
				cards=cards,
				api_key=args.api_key,
				keyword=args.keyword,
			)
			verified_run = True
			dropped = before - len(cards)
			if dropped:
				print(f"✅ 検証完了: {dropped}枚を誤読として棄却し、{len(cards)}枚が残りました。")
			else:
				print(f"✅ 検証完了: {len(cards)}枚すべてが検証を通過しました。")
		except Exception as e:
			print(f"⚠️ 弁護人レビューに失敗したため未検証の結果を表示します: {e}", file=sys.stderr)

	print(format_promise_terminal(speaker, cards))

	if args.sns:
		print("\n📋 SNS投稿用テキスト:")
		print("-" * 40)
		sns_text = format_promise_sns(speaker, cards, verified_run=verified_run)
		print(sns_text)
		print("-" * 40)
		print(f"（{len(sns_text)}文字）")

	if args.x:
		print("\n🐦 X（旧Twitter）投稿用テキスト:")
		print("-" * 40)
		print(format_promise_x(speaker, cards, verified_run=verified_run))
		print("-" * 40)

	if args.note is not None:
		note_text = format_promise_note(speaker, cards)
		if args.note == "-":
			print("\n📝 Note向けマークダウン:")
			print("=" * 40)
			print(note_text)
			print("=" * 40)
		else:
			output_path = args.note if args.note else f"{speaker}_promise_tracker.md"
			with open(output_path, "w", encoding="utf-8") as f:
				f.write(note_text)
			print(f"\n📝 Note向けマークダウンを保存しました: {output_path}")


if __name__ == "__main__":
	main()
