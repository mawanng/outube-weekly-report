"""
유튜브 주간 업로드 보고 봇
- 본채널 + 봉풀주(서브채널)의 지난 1주일(직전 일요일 0시 ~ 이번 일요일 0시, KST) 업로드를
  정리해서 디스코드 채널에 여러 메시지로 나눠 올린다.
- 분류 기준: 각 채널/카테고리별로 지정된 "재생목록"에 들어있는지로 판정한다.
    - 본채널: 쇼츠 재생목록에 있으면 쇼츠, 없으면 롱폼
    - 봉풀주: 쇼츠 재생목록 > 짧클립 재생목록 > 풀영상 재생목록 순으로 확인
      (셋 다 아니면 집계에서 제외 — 재생목록 정리를 안 한 영상은 보고서에 안 뜸)
- 본채널 롱폼 / 본채널·봉풀주 쇼츠는 영상 1개당 메시지 1개로 올리고, 봇이 그 메시지에
  선택지 이모지 반응(🅰️/🅱️)을 미리 달아둔다 — 사람이 그 이모지를 눌러 담당자를 표시하는 방식.
  (이 반응은 웹훅 권한으로는 못 달아서, 이 부분만 디스코드 봇 토큰으로 전송한다.)
- GitHub Actions 크론으로 매주 일요일 0시(KST)에 1회 실행되고 끝난다(상시 서버 불필요).
- 테스트용: REPORT_TEST_DATE 환경변수(YYYY-MM-DD)를 주면 그 날짜가 속한 주를 기준으로 계산한다.
"""

import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

import requests

from discord_common import (
    CREATOR_OPTIONS,
    DISCORD_API_BASE,
    DISCORD_BOT_TOKEN,
    DISCORD_CHANNEL_ID,
    REACTION_A,
    REACTION_B,
    THUMBNAILER_OPTIONS,
)

KST = timezone(timedelta(hours=9))
YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"
MARKDOWN_ESCAPE_PATTERN = re.compile(r"([\\`*_~|>\[\]])")

YOUTUBE_API_KEY = os.environ["YOUTUBE_API_KEY"]
DISCORD_WEBHOOK_URL = os.environ["DISCORD_WEBHOOK_URL"]

MAIN_CHANNEL_ID = os.environ.get("MAIN_CHANNEL_ID", "UCTifMx1ONpElK5x6B4ng8eg")
SUB_CHANNEL_ID = os.environ.get("SUB_CHANNEL_ID", "UCgGvSg2lscdNUx9ZJIBh9FQ")

# 분류용 재생목록 ID
MAIN_SHORTS_PLAYLIST_ID = os.environ.get(
    "MAIN_SHORTS_PLAYLIST_ID", "PLqE7uvTHaH30qYSSVmxsjV6JrlUUInv_f"
)
SUB_SHORTS_PLAYLIST_ID = os.environ.get(
    "SUB_SHORTS_PLAYLIST_ID", "PLQCedlY19W03Zpo5l4MYInmwqiqJQnO75"
)
SUB_CLIP_PLAYLIST_ID = os.environ.get(
    "SUB_CLIP_PLAYLIST_ID", "PLQCedlY19W00gv3uJ7OP_kqYQ-l2kFjQE"
)
SUB_LONG_PLAYLIST_ID = os.environ.get(
    "SUB_LONG_PLAYLIST_ID", "PLQCedlY19W03t8VSqAeRTleKHzXfSZpB7"
)

COLOR_MAIN = 0x57F287
COLOR_SUB = 0xEB459E
COLOR_TOTAL = 0xF1C40F


def fetch_channel_info(channel_id):
    """업로드 재생목록 ID + 채널명 + 프로필 사진 URL을 한 번에 가져온다."""
    r = requests.get(
        f"{YOUTUBE_API_BASE}/channels",
        params={"part": "snippet,contentDetails", "id": channel_id, "key": YOUTUBE_API_KEY},
        timeout=30,
    )
    r.raise_for_status()
    items = r.json().get("items", [])
    if not items:
        raise RuntimeError(f"채널을 찾을 수 없습니다: {channel_id}")
    item = items[0]
    return {
        "uploads_playlist_id": item["contentDetails"]["relatedPlaylists"]["uploads"],
        "title": item["snippet"]["title"],
        "thumbnail_url": item["snippet"]["thumbnails"]["default"]["url"],
    }


def fetch_recent_video_ids(playlist_id, start_utc, end_utc, max_pages=20):
    """uploads 재생목록은 최신 업로드가 먼저 나온다는 전제로, start<=publishedAt<end 인 영상만 수집."""
    video_ids = []
    page_token = None
    for _ in range(max_pages):
        r = requests.get(
            f"{YOUTUBE_API_BASE}/playlistItems",
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


def fetch_playlist_video_ids(playlist_id, max_pages=200):
    """재생목록에 들어있는 전체 영상 ID 집합(분류용, 추가된 순서 무관하게 끝까지 조회)."""
    video_ids = set()
    page_token = None
    for _ in range(max_pages):
        r = requests.get(
            f"{YOUTUBE_API_BASE}/playlistItems",
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
        for item in data.get("items", []):
            vid = item["contentDetails"].get("videoId")
            if vid:
                video_ids.add(vid)
        page_token = data.get("nextPageToken")
        if not page_token:
            break
    return video_ids


def fetch_video_details(video_ids):
    videos = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i : i + 50]
        r = requests.get(
            f"{YOUTUBE_API_BASE}/videos",
            params={"part": "snippet", "id": ",".join(batch), "key": YOUTUBE_API_KEY},
            timeout=30,
        )
        r.raise_for_status()
        videos.extend(r.json().get("items", []))
    return videos


def format_kst_date(published_at):
    dt = datetime.fromisoformat(published_at.replace("Z", "+00:00")).astimezone(KST)
    return f"{dt.month}/{dt.day}"


def escape_markdown(text):
    return MARKDOWN_ESCAPE_PATTERN.sub(r"\\\1", text)


def video_url(video):
    return f"https://www.youtube.com/watch?v={video['id']}"


def build_video_lines(videos):
    if not videos:
        return "＿ 없음"
    lines = []
    for v in sorted(videos, key=lambda x: x["snippet"]["publishedAt"]):
        title = escape_markdown(v["snippet"]["title"])
        date = format_kst_date(v["snippet"]["publishedAt"])
        lines.append(f"› [{title}]({video_url(v)})  `{date}`")
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


def week_date_range_label(this_sunday_kst):
    start = this_sunday_kst - timedelta(days=7)
    end = this_sunday_kst - timedelta(days=1)
    return f"{start.month}/{start.day} ~ {end.month}/{end.day}"


def classify_main(videos, shorts_ids):
    long_videos = [v for v in videos if v["id"] not in shorts_ids]
    shorts_videos = [v for v in videos if v["id"] in shorts_ids]
    return long_videos, shorts_videos


def classify_sub(videos, shorts_ids, clip_ids, long_ids):
    """쇼츠/짧클립/풀영상 재생목록 중 어디에도 없는 영상은 분류하지 않고 제외한다."""
    shorts_videos, clip_videos, long_videos = [], [], []
    for v in videos:
        vid = v["id"]
        if vid in shorts_ids:
            shorts_videos.append(v)
        elif vid in clip_ids:
            clip_videos.append(v)
        elif vid in long_ids:
            long_videos.append(v)
        else:
            print(f"[skip] 재생목록 미분류라 제외: {v['snippet']['title']}", file=sys.stderr)
    return long_videos, clip_videos, shorts_videos


def build_channel_header_embed(channel, title, color, date_range, has_uploads):
    """채널 카드(헤더). 업로드가 없으면 안내 문구도 같이 넣는다."""
    description = date_range if has_uploads else f"{date_range}\n＿ 이번 주 업로드 없음"
    return {
        "author": {"name": channel["title"], "icon_url": channel["thumbnail_url"]},
        "title": title,
        "description": description,
        "color": color,
    }


def build_sub_lists_embed(channel, sub_clip, sub_long, date_range):
    """봉풀주 짧클립/풀영상을 헤더+목록 전부 한 메시지로 합친다."""
    return {
        "author": {"name": channel["title"], "icon_url": channel["thumbnail_url"]},
        "title": "「 🎮 봉풀주 업로드 」",
        "description": date_range,
        "color": COLOR_SUB,
        "fields": [
            {"name": f"✂️ 짧클립  ({len(sub_clip)})", "value": build_video_lines(sub_clip)},
            {"name": f"🎬 풀영상  ({len(sub_long)})", "value": build_video_lines(sub_long)},
        ],
    }


def build_total_embed(main_long, main_shorts, sub_long, sub_clip, sub_shorts):
    return {
        "title": "「 📊 Total 」",
        "color": COLOR_TOTAL,
        "fields": [
            {"name": "👤 본채널 롱폼", "value": f"**{len(main_long)}**개", "inline": True},
            {"name": "👤 본채널 쇼츠", "value": f"**{len(main_shorts)}**개", "inline": True},
            {"name": "​", "value": "​", "inline": True},
            {"name": "🎮 봉풀주 풀영상", "value": f"**{len(sub_long)}**개", "inline": True},
            {"name": "🎮 봉풀주 짧클립", "value": f"**{len(sub_clip)}**개", "inline": True},
            {"name": "🎮 봉풀주 쇼츠", "value": f"**{len(sub_shorts)}**개", "inline": True},
        ],
        "footer": {"text": "메모는 각 메시지 우클릭 → 스레드 만들기로 남겨주세요"},
    }


def build_video_reaction_embed(channel, video, color, option_labels, show_thumbnail):
    label_a, label_b = option_labels
    embed = {
        "author": {"name": channel["title"], "icon_url": channel["thumbnail_url"]},
        "title": video["snippet"]["title"],
        "url": video_url(video),
        "description": f"`{format_kst_date(video['snippet']['publishedAt'])}`",
        "color": color,
        "footer": {"text": f"{REACTION_A} {label_a}    {REACTION_B} {label_b}"},
    }
    if show_thumbnail:
        thumbnails = video["snippet"].get("thumbnails", {})
        thumb = thumbnails.get("medium") or thumbnails.get("default")
        if thumb:
            embed["thumbnail"] = {"url": thumb["url"]}
    return embed


def send_embed_to_discord(embed):
    # thread_name은 포럼 채널 웹훅에서만 동작해서(일반 채널은 400 에러) 쓰지 않음.
    payload = {"embeds": [embed]}
    r = requests.post(DISCORD_WEBHOOK_URL, params={"wait": "true"}, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def send_header_to_discord(week_str):
    # 임베드 제목보다 더 크게 보이도록 일반 메시지의 마크다운 헤더(# )로 전송.
    payload = {"content": f"# 📅 {week_str} 주간 보고"}
    r = requests.post(DISCORD_WEBHOOK_URL, params={"wait": "true"}, json=payload, timeout=30)
    r.raise_for_status()
    return r.json()


def send_message_as_bot(embed):
    r = requests.post(
        f"{DISCORD_API_BASE}/channels/{DISCORD_CHANNEL_ID}/messages",
        headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}"},
        json={"embeds": [embed]},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def add_reaction(message_id, emoji):
    encoded = quote(emoji, safe="")
    r = requests.put(
        f"{DISCORD_API_BASE}/channels/{DISCORD_CHANNEL_ID}/messages/{message_id}/reactions/{encoded}/@me",
        headers={"Authorization": f"Bot {DISCORD_BOT_TOKEN}"},
        timeout=30,
    )
    r.raise_for_status()


def post_videos_with_reactions(channel, videos, color, option_labels, show_thumbnail):
    for v in sorted(videos, key=lambda x: x["snippet"]["publishedAt"]):
        embed = build_video_reaction_embed(channel, v, color, option_labels, show_thumbnail)
        message = send_message_as_bot(embed)
        add_reaction(message["id"], REACTION_A)
        add_reaction(message["id"], REACTION_B)


def resolve_now():
    test_date = os.environ.get("REPORT_TEST_DATE")
    if not test_date:
        return None
    year, month, day = (int(part) for part in test_date.split("-"))
    return datetime(year, month, day, 12, 0, tzinfo=KST)


def main():
    start_utc, end_utc, this_sunday_kst = get_week_range(resolve_now())
    week_str = week_label(this_sunday_kst)
    date_range = week_date_range_label(this_sunday_kst)

    main_channel = fetch_channel_info(MAIN_CHANNEL_ID)
    sub_channel = fetch_channel_info(SUB_CHANNEL_ID)

    main_ids = fetch_recent_video_ids(main_channel["uploads_playlist_id"], start_utc, end_utc)
    sub_ids = fetch_recent_video_ids(sub_channel["uploads_playlist_id"], start_utc, end_utc)

    main_videos = fetch_video_details(main_ids)
    sub_videos = fetch_video_details(sub_ids)

    main_shorts_ids = fetch_playlist_video_ids(MAIN_SHORTS_PLAYLIST_ID)
    sub_shorts_ids = fetch_playlist_video_ids(SUB_SHORTS_PLAYLIST_ID)
    sub_clip_ids = fetch_playlist_video_ids(SUB_CLIP_PLAYLIST_ID)
    sub_long_ids = fetch_playlist_video_ids(SUB_LONG_PLAYLIST_ID)

    main_long, main_shorts = classify_main(main_videos, main_shorts_ids)
    sub_long, sub_clip, sub_shorts = classify_sub(
        sub_videos, sub_shorts_ids, sub_clip_ids, sub_long_ids
    )

    # 1) 큰 주간 헤더
    send_header_to_discord(week_str)

    # 2) 본채널 — 채널 카드 하나 + 영상마다 메시지 1개(반응용).
    send_embed_to_discord(
        build_channel_header_embed(
            main_channel,
            "「 👤 본채널 업로드 」",
            COLOR_MAIN,
            date_range,
            has_uploads=bool(main_long or main_shorts),
        )
    )
    post_videos_with_reactions(
        main_channel, main_long, COLOR_MAIN, THUMBNAILER_OPTIONS, show_thumbnail=True
    )
    post_videos_with_reactions(
        main_channel, main_shorts, COLOR_MAIN, CREATOR_OPTIONS, show_thumbnail=False
    )

    # 3) 봉풀주 — 짧클립/풀영상은 한 메시지로 합치고, 쇼츠만 영상별 메시지(반응용).
    send_embed_to_discord(build_sub_lists_embed(sub_channel, sub_clip, sub_long, date_range))
    post_videos_with_reactions(
        sub_channel, sub_shorts, COLOR_SUB, CREATOR_OPTIONS, show_thumbnail=False
    )

    # 4) Total
    send_embed_to_discord(build_total_embed(main_long, main_shorts, sub_long, sub_clip, sub_shorts))


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"API 오류: {e} / 응답: {e.response.text}", file=sys.stderr)
        raise
