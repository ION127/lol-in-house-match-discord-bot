import os
from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN: str = os.getenv("DISCORD_TOKEN", "")
RIOT_API_KEY: str = os.getenv("RIOT_API_KEY", "")
DATABASE_PATH: str = os.getenv("DATABASE_PATH", "bot.db")

LOBBY_MAX_MEMBERS: int = 10
RIOT_REGION: str = "kr"        # 플랫폼 라우팅 (summoner/league API)
RIOT_REGIONAL: str = "asia"    # 리전 라우팅 (account/match API)

# 웹 관리자 페이지
WEB_PORT: int = int(os.getenv("WEB_PORT", "8080"))
WEB_SECRET: str = os.getenv("WEB_SECRET", "admin")

# /관리 명령어 사용 가능 유저 ID 목록
ADMIN_USER_IDS: set[int] = {
    343643531321147393,
    262454898774245377,
    893398453789536297,
    481429614498152448,
}
