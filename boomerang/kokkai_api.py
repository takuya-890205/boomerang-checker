"""国会会議録検索システム API クライアント

https://kokkai.ndl.go.jp/api.html の発言単位出力APIを使用して
指定した議員の国会発言を取得する。
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import date, timedelta

import requests

SPEECH_API_URL = "https://kokkai.ndl.go.jp/api/speech"
MEETING_API_URL = "https://kokkai.ndl.go.jp/api/meeting"
MAX_RECORDS_PER_REQUEST = 100
REQUEST_INTERVAL = 1.0  # サーバー負荷軽減のためのリクエスト間隔（秒）


@dataclass
class Speech:
	"""国会発言1件を表すデータクラス"""

	speaker: str
	date: str
	name_of_house: str  # 院名（衆議院/参議院）
	name_of_meeting: str  # 会議名
	speech_text: str  # 発言本文
	speech_url: str  # 発言URL
	session: int  # 国会回次
	speaker_position: str = ""  # 発言者肩書（例: 内閣総理大臣）
	speaker_group: str = ""  # 発言者所属会派
	issue_id: str = ""  # 会議録ID（前後文脈の取得に使用）
	speech_order: int = 0  # 会議内の発言順


def _fetch_speeches_in_range(
	speaker_name: str,
	max_speeches: int,
	from_date: str | None = None,
	until_date: str | None = None,
) -> list[Speech]:
	"""指定した期間の発言を取得するヘルパー関数。

	Args:
		speaker_name: 議員名
		max_speeches: 取得する最大発言数
		from_date: 取得開始日（YYYY-MM-DD形式、省略可）
		until_date: 取得終了日（YYYY-MM-DD形式、省略可）

	Returns:
		Speech オブジェクトのリスト
	"""
	speeches: list[Speech] = []
	start_record = 1
	records_per_request = min(max_speeches, MAX_RECORDS_PER_REQUEST)

	while len(speeches) < max_speeches:
		params: dict[str, str | int] = {
			"speaker": speaker_name,
			"recordPacking": "json",
			"startRecord": start_record,
			"maximumRecords": records_per_request,
		}
		if from_date:
			params["from"] = from_date
		if until_date:
			params["until"] = until_date

		response = requests.get(SPEECH_API_URL, params=params, timeout=30)
		response.raise_for_status()
		data = response.json()

		total_records = data.get("numberOfRecords", 0)
		if total_records == 0:
			break

		records = data.get("speechRecord", [])
		if not records:
			break

		for record in records:
			if len(speeches) >= max_speeches:
				break
			speeches.append(
				Speech(
					speaker=record.get("speaker", ""),
					date=record.get("date", ""),
					name_of_house=record.get("nameOfHouse", ""),
					name_of_meeting=record.get("nameOfMeeting", ""),
					speech_text=record.get("speech", ""),
					speech_url=record.get("speechURL", ""),
					session=int(record.get("session", 0)),
					speaker_position=record.get("speakerPosition") or "",
					speaker_group=record.get("speakerGroup") or "",
					issue_id=record.get("issueID") or "",
					speech_order=int(record.get("speechOrder") or 0),
				)
			)

		next_pos = data.get("nextRecordPosition")
		if next_pos is None or next_pos == 0:
			break
		start_record = next_pos

		# サーバー負荷軽減
		time.sleep(REQUEST_INTERVAL)

	return speeches


def fetch_speeches(
	speaker_name: str,
	max_speeches: int = 100,
	from_date: str | None = None,
	until_date: str | None = None,
	spread_years: bool = True,
) -> list[Speech]:
	"""指定した議員の発言を取得する。

	Args:
		speaker_name: 議員名（例: "岸田文雄"）
		max_speeches: 取得する最大発言数（デフォルト100）
		from_date: 取得開始日（YYYY-MM-DD形式、省略可）
		until_date: 取得終了日（YYYY-MM-DD形式、省略可）
		spread_years: True のとき、直近2年分と3〜10年前の発言を分けて取得してマージ。
		             from_date/until_date が明示指定された場合は従来通り動作する。

	Returns:
		Speech オブジェクトのリスト
	"""
	# from_date/until_date が明示指定された場合、または spread_years=False の場合は従来通り動作
	if from_date or until_date or not spread_years:
		return _fetch_speeches_in_range(
			speaker_name=speaker_name,
			max_speeches=max_speeches,
			from_date=from_date,
			until_date=until_date,
		)

	# spread_years=True のとき、期間を分割して取得する
	today = date.today()

	# 直近2年分（60%）
	recent_max = int(max_speeches * 0.6)
	recent_until = today.strftime("%Y-%m-%d")
	recent_from = (today - timedelta(days=365 * 2)).strftime("%Y-%m-%d")

	# 3〜10年前（40%）
	old_max = max_speeches - recent_max
	old_until = (today - timedelta(days=365 * 3)).strftime("%Y-%m-%d")
	old_from = (today - timedelta(days=365 * 10)).strftime("%Y-%m-%d")

	print(f"  📅 直近2年分（最大{recent_max}件）を取得中...")
	recent_speeches = _fetch_speeches_in_range(
		speaker_name=speaker_name,
		max_speeches=recent_max,
		from_date=recent_from,
		until_date=recent_until,
	)

	# サーバー負荷軽減のためリクエスト間を空ける
	time.sleep(REQUEST_INTERVAL)

	print(f"  📅 3〜10年前（最大{old_max}件）を取得中...")
	old_speeches = _fetch_speeches_in_range(
		speaker_name=speaker_name,
		max_speeches=old_max,
		from_date=old_from,
		until_date=old_until,
	)

	# 両方のリストをマージして返す
	merged = recent_speeches + old_speeches
	print(f"  🔀 マージ結果: 直近{len(recent_speeches)}件 + 過去{len(old_speeches)}件 = 計{len(merged)}件")
	return merged


def fetch_context_speeches(
	target: Speech,
	before: int = 2,
	after: int = 1,
) -> list[Speech]:
	"""指定した発言の前後の発言（質疑の文脈）を会議単位出力APIで取得する。

	切り抜き防止のための文脈検証に使う。取得に失敗した場合は空リストを返す
	（文脈なしでも検証自体は続行できるようにするため）。

	Args:
		target: 対象の発言（issue_id と speech_order が設定されていること）
		before: 対象より前の発言を何件取るか
		after: 対象より後の発言を何件取るか

	Returns:
		対象を除く前後の Speech リスト（発言順ソート済み）
	"""
	if not target.issue_id or target.speech_order <= 0:
		return []

	params: dict[str, str | int] = {
		"issueID": target.issue_id,
		"recordPacking": "json",
		"maximumRecords": 1,
	}
	try:
		response = requests.get(MEETING_API_URL, params=params, timeout=60)
		response.raise_for_status()
		data = response.json()
		meetings = data.get("meetingRecord", [])
		if not meetings:
			return []
		records = meetings[0].get("speechRecord", [])
	except (requests.RequestException, ValueError):
		return []

	lo = target.speech_order - before
	hi = target.speech_order + after
	context: list[Speech] = []
	for record in records:
		order = int(record.get("speechOrder") or 0)
		if order < lo or order > hi or order == target.speech_order:
			continue
		context.append(
			Speech(
				speaker=record.get("speaker") or "",
				date=meetings[0].get("date", ""),
				name_of_house=meetings[0].get("nameOfHouse", ""),
				name_of_meeting=meetings[0].get("nameOfMeeting", ""),
				speech_text=record.get("speech", ""),
				speech_url=record.get("speechURL", ""),
				session=int(meetings[0].get("session") or 0),
				speaker_position=record.get("speakerPosition") or "",
				speaker_group=record.get("speakerGroup") or "",
				issue_id=target.issue_id,
				speech_order=order,
			)
		)

	context.sort(key=lambda s: s.speech_order)
	return context


def truncate_speech(text: str, max_chars: int = 500) -> str:
	"""発言テキストを指定文字数で切り詰める"""
	# HTMLタグの簡易除去
	import re

	text = re.sub(r"<[^>]+>", "", text)
	text = text.strip()
	if len(text) <= max_chars:
		return text
	return text[:max_chars] + "..."
