"""report.py와 poll_reactions.py가 공유하는 디스코드 관련 상수/설정."""

import os

DISCORD_API_BASE = "https://discord.com/api/v10"

DISCORD_BOT_TOKEN = os.environ["DISCORD_BOT_TOKEN"]
DISCORD_CHANNEL_ID = os.environ["DISCORD_CHANNEL_ID"]

# 선택지 순서대로 매칭되는 반응 이모지. 3번째부터는 알파벳 대신 숫자 이모지를 이어 씀
# (🅰️/🅱️ 다음 "C" 문자 이모지는 유니코드에 없음).
REACTION_EMOJIS = ["🅰️", "🅱️", "3️⃣", "4️⃣", "5️⃣"]



def _options_from_env(name):
    """담당자 이름은 공개 저장소에 남기지 않으려고 GitHub Secrets에 쉼표로 구분해 저장한다.
    예: THUMBNAILER_OPTIONS = "이름1,이름2"
    각 이름을 Actions 로그 마스킹 대상으로 등록해서, 공개 로그에 찍혀도 ***로 가려지게 한다."""
    names = tuple(x.strip() for x in os.environ[name].split(",") if x.strip())
    if os.environ.get("GITHUB_ACTIONS") == "true":
        for n in names:
            print(f"::add-mask::{n}")
    return names


THUMBNAILER_OPTIONS = _options_from_env("THUMBNAILER_OPTIONS")
CREATOR_OPTIONS = _options_from_env("CREATOR_OPTIONS")

# pending_reactions.json에는 이름 대신 이 키만 저장한다(파일이 공개 저장소에 커밋되므로).
OPTION_SETS = {"thumbnailer": THUMBNAILER_OPTIONS, "creator": CREATOR_OPTIONS}
