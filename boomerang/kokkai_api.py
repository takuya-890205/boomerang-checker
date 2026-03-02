"""国会会議録検索システム API クライアント

https://kokkai.ndl.go.jp/api.html の発言単位出力APIを使用して
指定した議員の国会発言を取得する。
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import requests

SPEECH_API_URL = "https://kokkai.ndl.go.jp/api/speech"
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


def fetch_speeches(
    speaker_name: str,
    max_speeches: int = 100,
    from_date: str | None = None,
    until_date: str | None = None,
) -> list[Speech]:
    """指定した議員の発言を取得する。

    Args:
        speaker_name: 議員名（例: "岸田文雄"）
        max_speeches: 取得する最大発言数（デフォルト100）
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
                )
            )

        next_pos = data.get("nextRecordPosition")
        if next_pos is None or next_pos == 0:
            break
        start_record = next_pos

        # サーバー負荷軽減
        time.sleep(REQUEST_INTERVAL)

    return speeches


def truncate_speech(text: str, max_chars: int = 500) -> str:
    """発言テキストを指定文字数で切り詰める"""
    # HTMLタグの簡易除去
    import re

    text = re.sub(r"<[^>]+>", "", text)
    text = text.strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "..."
