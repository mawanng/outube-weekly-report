"""
반응 확정 처리 봇
- report.py가 올린 영상별 메시지(🅰️/🅱️ 반응이 달린 것)를 주기적으로 훑어서,
  누군가 실제로 반응을 눌렀으면 그 메시지를 "{카테고리}: {선택}"으로 수정하고
  반응(이모지)을 전부 지운다.
- GitHub Actions 크론으로 몇 분 간격 반복 실행되는 방식(상시 서버 아님) — 그래서
  클릭이 반영되기까지 최대 폴링 간격만큼 지연이 있을 수 있다.
"""

import sys
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

HEADERS = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}

# footer 텍스트 -> (카테고리명, 옵션A 라벨, 옵션B 라벨)
PENDING_FOOTERS = {
    f"{REACTION_A} {label_a}    {REACTION_B} {label_b}": (category, label_a, label_b)
    for category, (label_a, label_b) in (
        ("썸네일러", THUMBNAILER_OPTIONS),
        ("제작자", CREATOR_OPTIONS),
    )
}


def get_bot_user_id():
    r = requests.get(f"{DISCORD_API_BASE}/users/@me", headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.json()["id"]


def fetch_recent_messages():
    r = requests.get(
        f"{DISCORD_API_BASE}/channels/{DISCORD_CHANNEL_ID}/messages",
        headers=HEADERS,
        params={"limit": 100},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def fetch_reaction_users(message_id, emoji):
    encoded = quote(emoji, safe="")
    r = requests.get(
        f"{DISCORD_API_BASE}/channels/{DISCORD_CHANNEL_ID}/messages/{message_id}/reactions/{encoded}",
        headers=HEADERS,
        params={"limit": 10},
        timeout=30,
    )
    r.raise_for_status()
    return r.json()


def find_decision(message, bot_user_id):
    """이 메시지의 🅰️/🅱️ 반응 중 봇이 아닌 사람이 누른 게 있으면 label_index(0 또는 1) 반환."""
    reactions = {r["emoji"]["name"]: r["count"] for r in message.get("reactions", [])}
    for emoji, label_index in ((REACTION_A, 0), (REACTION_B, 1)):
        if reactions.get(emoji, 0) < 2:  # 봇 자신의 반응(count=1)만 있으면 아직 미확정
            continue
        users = fetch_reaction_users(message["id"], emoji)
        if any(user["id"] != bot_user_id for user in users):
            return label_index
    return None


def confirm_message(message, label_index):
    embed = message["embeds"][0]
    category, label_a, label_b = PENDING_FOOTERS[embed["footer"]["text"]]
    chosen_label = label_a if label_index == 0 else label_b
    embed["footer"] = {"text": f"{category}: {chosen_label}"}

    r = requests.patch(
        f"{DISCORD_API_BASE}/channels/{DISCORD_CHANNEL_ID}/messages/{message['id']}",
        headers=HEADERS,
        json={"embeds": [embed]},
        timeout=30,
    )
    r.raise_for_status()

    r = requests.delete(
        f"{DISCORD_API_BASE}/channels/{DISCORD_CHANNEL_ID}/messages/{message['id']}/reactions",
        headers=HEADERS,
        timeout=30,
    )
    r.raise_for_status()


def is_pending(message, bot_user_id):
    if message.get("author", {}).get("id") != bot_user_id:
        return False
    embeds = message.get("embeds") or []
    if not embeds or "footer" not in embeds[0]:
        return False
    return embeds[0]["footer"]["text"] in PENDING_FOOTERS


def main():
    bot_user_id = get_bot_user_id()
    messages = fetch_recent_messages()

    confirmed = 0
    for message in messages:
        if not is_pending(message, bot_user_id):
            continue
        label_index = find_decision(message, bot_user_id)
        if label_index is None:
            continue
        confirm_message(message, label_index)
        confirmed += 1
        print(f"[확정] {message['embeds'][0].get('title', message['id'])}")

    if confirmed == 0:
        print("확정할 반응 없음")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"API 오류: {e} / 응답: {e.response.text}", file=sys.stderr)
        raise
