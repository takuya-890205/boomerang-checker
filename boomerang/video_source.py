"""YouTubeノーカット動画の文字起こしソース取り込みモジュール

党首会見のぶら下がり・街頭演説など、公式の全文書き起こしが存在しない発言を
YouTubeの字幕（自動生成含む）から取り込み、Speech として検出パイプラインに流す。

設計メモ:
- 対象は公式チャンネル等の「ノーカット動画」。切り抜き動画は引用の錨にしない
  （どの動画を渡すかは利用者の責任。ツール側では判定できないため格付けは B）。
- 自動字幕には誤起こしがある（例: 「水岡代表」→「田代表」）。照合
  （analyzer.match_excerpt）は句読点・空白を無視した正規化ストリームで行われるが、
  誤起こし自体は救えないため、引用は必ず動画で確認できる形（URL常時表示）を保つ。
- セグメントの開始秒はモジュール内にキャッシュし、抜粋→タイムスタンプURLの
  逆引き（timestamp_url）に使う。
"""

from __future__ import annotations

import re

from boomerang.kokkai_api import Speech

# 動画IDごとの字幕セグメント [(開始秒, テキスト), ...]（タイムスタンプ逆引き用）
_SEGMENT_CACHE: dict[str, list[tuple[float, str]]] = {}


def parse_video_arg(arg: str) -> tuple[str, str]:
	"""--video の引数をパースする。

	"YYYY-MM-DD:https://..." 形式なら日付を分離、そうでなければURL/動画IDのみ
	（日付は動画メタデータの公開日から自動取得を試みる）。

	Returns:
		(url_or_id, date)  date は空文字の場合あり
	"""
	m = re.match(r"^(\d{4}-\d{2}-\d{2}):(.+)$", arg)
	if m:
		return m.group(2), m.group(1)
	return arg, ""


def _extract_video_id(video: str) -> str | None:
	"""YouTube URLまたは動画IDから動画IDを取り出す。"""
	if re.fullmatch(r"[A-Za-z0-9_-]{11}", video):
		return video
	for pattern in (
		r"[?&]v=([A-Za-z0-9_-]{11})",
		r"youtu\.be/([A-Za-z0-9_-]{11})",
		r"youtube\.com/(?:live|shorts|embed)/([A-Za-z0-9_-]{11})",
	):
		m = re.search(pattern, video)
		if m:
			return m.group(1)
	return None


def _fetch_metadata(video_id: str) -> tuple[str, str, str]:
	"""yt-dlpで動画のタイトル・公開日・チャンネル名を取得する。失敗時は空文字。"""
	try:
		import yt_dlp

		opts = {"quiet": True, "no_warnings": True, "noprogress": True, "skip_download": True}
		with yt_dlp.YoutubeDL(opts) as ydl:
			info = ydl.extract_info(f"https://www.youtube.com/watch?v={video_id}", download=False)
		title = info.get("title") or ""
		upload = info.get("upload_date") or ""  # YYYYMMDD
		date = f"{upload[:4]}-{upload[4:6]}-{upload[6:8]}" if len(upload) == 8 else ""
		channel = info.get("channel") or ""
		return title, date, channel
	except Exception as e:
		print(f"  ⚠️ 動画メタデータの取得に失敗しました（続行します）: {e}")
		return "", "", ""


def fetch_video_transcript(
	speaker_name: str,
	video: str,
	date: str = "",
) -> Speech | None:
	"""YouTube動画の字幕を取得して Speech に変換する。

	Args:
		speaker_name: 議員名
		video: 動画URLまたは動画ID（公式チャンネルのノーカット動画を渡すこと）
		date: 発言日（YYYY-MM-DD。空なら動画の公開日を使う）

	Returns:
		Speech（source="動画文字起こし", grade="B"）。取得失敗時は None。
	"""
	video_id = _extract_video_id(video)
	if video_id is None:
		print(f"  ⚠️ YouTube動画IDを特定できませんでした: {video}")
		return None

	try:
		from youtube_transcript_api import YouTubeTranscriptApi

		fetched = YouTubeTranscriptApi().fetch(video_id, languages=["ja"])
		segments = [(s.start, s.text.strip()) for s in fetched if s.text.strip()]
	except Exception as e:
		print(f"  ⚠️ 字幕の取得に失敗しました（字幕なし動画は faster-whisper 対応を検討）: {video_id} ({e})")
		return None

	if not segments:
		print(f"  ⚠️ 字幕が空でした: {video_id}")
		return None
	_SEGMENT_CACHE[video_id] = segments

	title, upload_date, channel = _fetch_metadata(video_id)
	resolved_date = date or upload_date
	if not resolved_date:
		print(f"  ⚠️ 発言日を特定できませんでした（--video 'YYYY-MM-DD:URL' 形式で指定できます）: {video_id}")

	label = "YouTube動画文字起こし"
	if channel or title:
		label += f"・{channel}「{title}」" if channel else f"・「{title}」"

	text = "\n".join(t for _, t in segments)
	return Speech(
		speaker=speaker_name,
		date=resolved_date,
		name_of_house="",
		name_of_meeting=label,
		speech_text=text,
		speech_url=f"https://www.youtube.com/watch?v={video_id}",
		session=0,
		source="動画文字起こし",
		source_grade="B",
	)


def timestamp_url(video_url: str, excerpt: str) -> str | None:
	"""抜粋の冒頭を含むセグメントを探し、タイムスタンプ付き動画URLを返す。

	同一プロセス内で fetch_video_transcript 済みの動画のみ対応（セグメントは
	モジュール内キャッシュから引く）。見つからなければ None。
	"""
	video_id = _extract_video_id(video_url)
	segments = _SEGMENT_CACHE.get(video_id or "")
	if not segments or not excerpt:
		return None

	head = re.sub(r"[\s、。．，,\.]", "", excerpt)[:12]
	if not head:
		return None
	for start, seg_text in segments:
		if head[:6] in re.sub(r"[\s、。．，,\.]", "", seg_text):
			return f"https://www.youtube.com/watch?v={video_id}&t={int(start)}s"
	return None
