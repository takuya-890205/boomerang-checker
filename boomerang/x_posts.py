"""本人X（Twitter）ポストのソース取り込みモジュール

政治家本人のXアカウントの投稿を取得し、Speech として国会発言と同じ
パイプライン（検出→原文照合→弁護人レビュー）に流す。
本人の発信そのものなので報道による切り取りの影響を受けない一次ソース（グレードA）。

取得エンジンは ~/claude_workspace/tools/x_deep_dive/ の bird-search ドライバを流用する
（認証: ~/claude_workspace/.env の AUTH_TOKEN / CT0）。基盤が無い環境では
警告を出して空リストを返す（国会議事録のみで動作継続）。
"""

from __future__ import annotations

import re
import sys
import time
from pathlib import Path

from boomerang.kokkai_api import Speech

X_DEEP_DIVE_DIR = Path.home() / "claude_workspace" / "tools" / "x_deep_dive"

MAX_PAGES = 5  # ページネーション上限（1ページ約20件）


def _load_driver():
	"""x_deep_dive の検索ドライバを遅延インポートする（無い環境では None）"""
	if not X_DEEP_DIVE_DIR.exists():
		return None
	sys.path.insert(0, str(X_DEEP_DIVE_DIR))
	try:
		import x_dump  # type: ignore

		return x_dump
	except ImportError:
		return None


def _to_date(created_at: str) -> str:
	"""X の createdAt（例: 'Wed Nov 04 10:00:00 +0000 2025' or ISO）を YYYY-MM-DD にする"""
	from datetime import datetime

	if not created_at:
		return ""
	for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%S%z"):
		try:
			return datetime.strptime(created_at, fmt).strftime("%Y-%m-%d")
		except ValueError:
			continue
	# 先頭が YYYY-MM-DD 形式ならそのまま使う
	m = re.match(r"(\d{4}-\d{2}-\d{2})", created_at)
	return m.group(1) if m else ""


def fetch_x_posts(
	speaker_name: str,
	handle: str,
	keyword: str | None = None,
	max_posts: int = 40,
) -> list[Speech]:
	"""本人Xアカウントの投稿を取得して Speech リストに変換する。

	Args:
		speaker_name: 議員名（表示用。Speech.speaker に入れる）
		handle: Xのアカウント名（@ 有無どちらでも可）
		keyword: 争点キーワード（指定時は本文検索で絞り込む）
		max_posts: 取得する最大投稿数

	Returns:
		Speech のリスト（source="本人Xポスト", grade="A"）。
		基盤が無い・認証切れ・エラー時は警告を出して空リスト。
	"""
	x_dump = _load_driver()
	if x_dump is None:
		print("  ⚠️ x_deep_dive 基盤が見つからないため、本人Xポストの取得をスキップします")
		return []

	x_dump.load_env()
	auth = x_dump.check_auth()
	if not auth.get("authenticated"):
		print("  ⚠️ X認証が切れています（AUTH_TOKEN/CT0）。本人Xポストの取得をスキップします")
		return []

	handle = handle.lstrip("@")
	query = f"from:{handle}"
	if keyword:
		query += f" {keyword}"

	speeches: list[Speech] = []
	seen: set[str] = set()
	cursor = None
	for _ in range(MAX_PAGES):
		res = x_dump.run_driver(query, count=20, cursor=cursor)
		if not res.get("success"):
			print(f"  ⚠️ X取得エラー: {str(res.get('error', ''))[:120]}")
			break
		tweets = res.get("tweets", [])
		new = 0
		for t in tweets:
			tid = t.get("id", "")
			if not tid or tid in seen:
				continue
			seen.add(tid)
			new += 1
			# リポストは本人の主張と限らないため除外（引用RTは本文があるので残す）
			text = (t.get("text") or "").strip()
			if not text or text.startswith("RT @"):
				continue
			speeches.append(
				Speech(
					speaker=speaker_name,
					date=_to_date(t.get("createdAt", "")),
					name_of_house="X",
					name_of_meeting="本人ポスト",
					speech_text=text,
					speech_url=t.get("url", "") or f"https://x.com/{handle}/status/{tid}",
					session=0,
					speaker_position="",
					speaker_group="",
					source="本人Xポスト",
					source_grade="A",
				)
			)
			if len(speeches) >= max_posts:
				break
		if len(speeches) >= max_posts:
			break
		cursor = res.get("nextCursor")
		if not cursor or not tweets or new == 0:
			break
		time.sleep(getattr(x_dump, "SLEEP_SEC", 2))

	return speeches
