"""report.py와 poll_reactions.py가 공유하는 디스코드 관련 상수/설정."""

import os

DISCORD_API_BASE = "https://discord.com/api/v10"

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_CHANNEL_ID = os.environ["DISCORD_CHANNEL_ID"]

# 선택지 순서대로 매칭되는 반응 이모지. 3번째부터는 알파벳 대신 숫자 이모지를 이어 씀
# (🅰️/🅱️ 다음 "C" 문자 이모지는 유니코드에 없음).
REACTION_EMOJIS = ["🅰️", "🅱️", "3️⃣", "4️⃣", "5️⃣"]

THUMBNAILER_OPTIONS = ("카페인", "멜로크론")
CREATOR_OPTIONS = ("박정현", "상상", "쇼츠팀")
