"""report.py와 poll_reactions.py가 공유하는 디스코드 관련 상수/설정."""

import os

DISCORD_API_BASE = "https://discord.com/api/v10"

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_CHANNEL_ID = os.environ["DISCORD_CHANNEL_ID"]

REACTION_A = "🅰️"
REACTION_B = "🅱️"

# (옵션 A 라벨, 옵션 B 라벨)
THUMBNAILER_OPTIONS = ("카페인", "멜로크론")
CREATOR_OPTIONS = ("박정현", "상상")
