"""
유튜브 주간 업로드 보고 봇
- 본채널 + 봉풀주(서브채널)의 지난 1주일(직전 일요일 0시 ~ 이번 일요일 0시, KST) 업로드를
  풀영상/쇼츠로 나눠 디스코드 채널에 보고 메시지로 올린다.
- 쇼츠 판정: 제목/설명/태그에 "#shorts"가 포함되어 있으면 쇼츠로 취급.
- GitHub Actions 크론으로 매주 일요일 0시(KST)에 1회 실행되고 끝난다(상시 서버 불필요).
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

import requests

KST = timezone(timedelta(hours=9))
API_BASE = "https://www.googleapis.com/youtube/v3"
SHORTS_PATTERN = re.compile(r"#shorts", re.IGNORECASE)

YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

MAIN_CHANNEL_ID = os.environ.get("MAIN_CHANNEL_ID", "UCTifMx1ONpElK5x6B4ng8eg")
SUB_CHANNEL_ID = os.environ.get("SUB_CHANNEL_ID", "UCgGvSg2lscdNUx9ZJIBh9FQ")


def get_uploads_playlist_id(channel_id):
    r = requests.get(
        f"{API_BASE}/channels",
        params={"part": "contentDetails", "id": channel_id, "key": YOUTUBE_API_KEY},
        timeout=30,
    )
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        raise RuntimeError(f"채널을 찾을 수 없습니다: {channel_id}")
    return items[0]["contentDetails"]["relatedPlaylists"]["uploads"]


def fetch_recent_video_ids(playlist_id, start_utc, end_utc, max_pages=20):
    """uploads 재생목록은 최신 업로드가 먼저 나온다는 전제로, start<=publishedAt<end 인 영상만 수집."""
    video_ids = []
    page_token = None
    for _ in range(max_pages):
        r = requests.get(
            f"{API_BASE}/playlistItems",
            params={
                "part": "contentDetails",
                "playlistId": playlist_id,
                "maxResults": 50,
                "pageToken": page_token,
                "key": YOUTUBE_API_KEY,
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        stop = False
        for item in data.get("items", []):
            published_at = item["contentDetails"].get("videoPublishedAt")
            if not published_at:
                continue
            dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            if dt >= end_utc:
                continue
            if dt < start_utc:
                stop = True
                break
            video_ids.append(item["contentDetails"]["videoId"])
        if stop:
            break
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return video_ids


def fetch_video_details(video_ids):
    videos = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        r = requests.get(
            f"{API_BASE}/videos",
            params={"part": "snippet", "id": ",".join(batch), "key": YOUTUBE_API_KEY},
            timeout=30,
        )
        r.raise_for_status()
        videos.extend(r.json().get("items", []))
    return videos


def is_shorts(video):
    snippet = video["snippet"]
    title = snippet.get("title", "")
    description = snippet.get("description", "")
    tags = snippet.get("tags", []) or []
    if SHORTS_PATTERN.search(title) or SHORTS_PATTERN.search(description):
        return True
    return any(SHORTS_PATTERN.search(t) for t in tags)


def format_kst_date(published_at):
    dt = datetime.fromisoformat(published_at.replace("Z", "+00:00")).astimezone(KST)
    return f"{dt.month}/{dt.day}"


def build_video_lines(videos, with_thumbnailer=False):
    if not videos:
        return "없음"
    lines = []
    for v in sorted(videos, key=lambda x: x["snippet"]["publishedAt"]):
        title = v["snippet"]["title"]
        date = format_kst_date(v["snippet"]["publishedAt"])
        line = f"• {title} ({date}) ( )"
        if with_thumbnailer:
            line += " ( )"
        lines.append(line)
    return "\n".join(lines)


def get_week_range(now=None):
    """(start_utc, end_utc, this_sunday_kst) — [start, end) = 직전 일요일 0시 ~ 이번 일요일 0시 KST"""
    now = now or datetime.now(KST)
    days_since_sunday = (now.weekday() + 1) % 7  # 월=0..일=6 -> 일요일 기준 0
    this_sunday = (now - timedelta(days=days_since_sunday)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    last_sunday = this_sunday - timedelta(days=7)
    return last_sunday.astimezone(timezone.utc), this_sunday.astimezone(timezone.utc), this_sunday


def week_label(this_sunday_kst):
    start = this_sunday_kst - timedelta(days=7)
    week_of_month = (start.day - 1) // 7 + 1
    return f"{start.month}월 {week_of_month}주"


def build_report(main_videos, sub_videos, week_str):
    main_long = [v for v in main_videos if not is_shorts(v)]
    main_shorts = [v for v in main_videos if is_shorts(v)]
    sub_long = [v for v in sub_videos if not is_shorts(v)]
    sub_shorts = [v for v in sub_videos if is_shorts(v)]

    total_long = len(main_long) + len(sub_long)
    total_shorts = len(main_shorts) + len(sub_shorts)

    embed = {
        "title": f"📅 {week_str} 주간 보고",
        "color": 0x5865F2,
        "fields": [
            {"name": "🎮 봉풀주 - 풀영상", "value": build_video_lines(sub_long)},
            {"name": "🎮 봉풀주 - 쇼츠", "value": build_video_lines(sub_shorts)},
            {
                "name": "👤 본채널 - 영상(롱폼)",
                "value": build_video_lines(main_long, with_thumbnailer=True),
            },
            {
                "name": "👤 본채널 - 쇼츠",
                "value": build_video_lines(main_shorts, with_thumbnailer=True),
            },
            {"name": "📊 Total", "value": f"풀영상 {total_long}개\n쇼츠 {total_shorts}개"},
        ],
        "footer": {"text": "메모 / 썸네일러 이름은 이 메시지 우클릭 → 스레드 만들기로 남겨주세요"},
    }
    return embed


def send_to_discord(embed):
    # thread_name은 포럼 채널 웹훅에서만 동작해서(일반 텍스트 채널은 400 에러) 쓰지 않음.
    # 메모/썸네일러 이름을 남기고 싶으면 이 메시지를 우클릭 -> "스레드 만들기"로 직접 시작하면 됨.
    payload = {"embeds": [embed]}
    r = requests.post(DISCORD_WEBHOOK_URL, params={"wait": "true"}, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def main():
    start_utc, end_utc, this_sunday_kst = get_week_range()
    week_str = week_label(this_sunday_kst)

    main_playlist = get_uploads_playlist_id(MAIN_CHANNEL_ID)
    sub_playlist = get_uploads_playlist_id(SUB_CHANNEL_ID)

    main_ids = fetch_recent_video_ids(main_playlist, start_utc, end_utc)
    sub_ids = fetch_recent_video_ids(sub_playlist, start_utc, end_utc)

    main_videos = fetch_video_details(main_ids)
    sub_videos = fetch_video_details(sub_ids)

    embed = build_report(main_videos, sub_videos, week_str)
    result = send_to_discord(embed)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"API 오류: {e} / 응답: {e.response.text}", file=sys.stderr)
        raise
