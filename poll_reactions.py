"""
반응 확정 처리 봇
- report.py가 pending_reactions.json에 적어둔 "선택용 카드 -> 정리본 메시지" 매핑을 보고,
  누군가 실제로 선택지 이모지(🅰️/🅱️/3️⃣ ...) 반응을 눌렀으면:
    1) 정리본 메시지에서 그 영상 줄의 "(카테고리: )" 자리표시를 "(카테고리: 선택값)"으로 채우고
    2) 선택용 카드 메시지는 삭제하고
    3) pending_reactions.json에서 그 항목을 지운다.
- GitHub Actions 크론으로 몇 분 간격 반복 실행되는 방식(상시 서버 아님) — 그래서
  클릭이 반영되기까지 최대 폴링 간격만큼 지연이 있을 수 있다.
"""

import json
import re
import sys
import time
from urllib.parse import quote

import requests

from discord_common import DISCORD_API_BASE, DISCORD_BOT_TOKEN, DISCORD_CHANNEL_ID, REACTION_EMOJIS

PENDING_FILE = "pending_reactions.json"


def load_pending():
    try:
        with open(PENDING_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save_pending(pending):
    with open(PENDING_FILE, "w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)


def discord_bot_request(method, url, **kwargs):
    """디스코드 봇 API 호출 + 429(rate limit)는 retry_after만큼 기다렸다가 자동 재시도."""
    headers = {"Authorization": f"Bot {DISCORD_BOT_TOKEN}"}
    while True:
        r = requests.request(method, url, headers=headers, timeout=30, **kwargs)
        if r.status_code == 429:
            retry_after = r.json().get("retry_after", 1)
            time.sleep(retry_after + 0.1)
            continue
        if r.status_code == 404:
            return None  # 메시지가 이미 지워졌거나 없음
        r.raise_for_status()
        return r


def get_bot_user_id():
    r = discord_bot_request("GET", f"{DISCORD_API_BASE}/users/@me")
    return r.json()["id"]


def fetch_message(message_id):
    r = discord_bot_request(
        "GET", f"{DISCORD_API_BASE}/channels/{DISCORD_CHANNEL_ID}/messages/{message_id}"
    )
    return r.json() if r else None


def fetch_reaction_users(message_id, emoji):
    encoded = quote(emoji, safe="")
    r = discord_bot_request(
        "GET",
        f"{DISCORD_API_BASE}/channels/{DISCORD_CHANNEL_ID}/messages/{message_id}/reactions/{encoded}",
        params={"limit": 10},
    )
    return r.json() if r else []


def find_decision(message, bot_user_id, num_options):
    """이 메시지의 선택지 반응 중 봇이 아닌 사람이 누른 게 있으면 label_index 반환."""
    reactions = {r["emoji"]["name"]: r["count"] for r in message.get("reactions", [])}
    for label_index, emoji in enumerate(REACTION_EMOJIS[:num_options]):
        if reactions.get(emoji, 0) < 2:  # 봇 자신의 반응(count=1)만 있으면 아직 미확정
            continue
        users = fetch_reaction_users(message["id"], emoji)
        if any(user["id"] != bot_user_id for user in users):
            return label_index
    return None


def fill_in_summary(summary_message_id, video_id, category, chosen_label):
    message = fetch_message(summary_message_id)
    if message is None:
        return False

    marker = f"/watch?v={video_id})"
    placeholder_pattern = re.compile(rf"\({re.escape(category)}: [^)]*\)$", re.MULTILINE)

    updated = False
    for field in message["embeds"][0].get("fields", []):
        lines = field["value"].split("\n")
        for i, line in enumerate(lines):
            if marker in line and placeholder_pattern.search(line):
                lines[i] = placeholder_pattern.sub(f"({category}: {chosen_label})", line)
                updated = True
        field["value"] = "\n".join(lines)

    if not updated:
        return False

    discord_bot_request(
        "PATCH",
        f"{DISCORD_API_BASE}/channels/{DISCORD_CHANNEL_ID}/messages/{summary_message_id}",
        json={"embeds": message["embeds"]},
    )
    return True


def delete_message(message_id):
    discord_bot_request(
        "DELETE", f"{DISCORD_API_BASE}/channels/{DISCORD_CHANNEL_ID}/messages/{message_id}"
    )


def main():
    pending = load_pending()
    if not pending:
        print("대기 중인 항목 없음")
        return

    bot_user_id = get_bot_user_id()
    confirmed_ids = []

    for card_message_id, entry in pending.items():
        message = fetch_message(card_message_id)
        if message is None:
            # 카드가 이미 지워졌으면(예: 수동 삭제) 대기 목록에서도 정리
            confirmed_ids.append(card_message_id)
            continue

        label_index = find_decision(message, bot_user_id, len(entry["labels"]))
        if label_index is None:
            continue

        chosen_label = entry["labels"][label_index]
        filled = fill_in_summary(
            entry["summary_message_id"], entry["video_id"], entry["category"], chosen_label
        )
        if filled:
            delete_message(card_message_id)
            confirmed_ids.append(card_message_id)
            print(f"[확정] {entry['video_id']} -> {entry['category']}: {chosen_label}")
        else:
            print(
                f"[경고] 정리본에서 {entry['video_id']} 줄을 못 찾음 (summary={entry['summary_message_id']})",
                file=sys.stderr,
            )

    for card_message_id in confirmed_ids:
        pending.pop(card_message_id, None)

    if confirmed_ids:
        save_pending(pending)
    else:
        print("확정할 반응 없음")


if __name__ == "__main__":
    try:
        main()
    except requests.HTTPError as e:
        print(f"API 오류: {e} / 응답: {e.response.text}", file=sys.stderr)
        raise
