import aiohttp
from typing import Optional
from config import RIOT_API_KEY, RIOT_REGION, RIOT_REGIONAL

_HEADERS = {"X-Riot-Token": RIOT_API_KEY}

# ── 표시용 상수 ────────────────────────────────────────────────────────────────

TIER_KO: dict[str, str] = {
    "IRON": "아이언",
    "BRONZE": "브론즈",
    "SILVER": "실버",
    "GOLD": "골드",
    "PLATINUM": "플래티넘",
    "EMERALD": "에메랄드",
    "DIAMOND": "다이아몬드",
    "MASTER": "마스터",
    "GRANDMASTER": "그랜드마스터",
    "CHALLENGER": "챌린저",
    "UNRANKED": "언랭크",
}

TIER_EMOJI: dict[str, str] = {
    "IRON":        "<:iron:1520784448743014570>",
    "BRONZE":      "<:bronze:1520784363367956510>",
    "SILVER":      "<:silver:1520784570977357915>",
    "GOLD":        "<:gold:1520784430447464468>",
    "PLATINUM":    "<:platinum:1520784481072582686>",
    "EMERALD":     "<:emerald:1520784416287363184>",
    "DIAMOND":     "<:diamond:1520784402768990279>",
    "MASTER":      "<:master1:1520784465100538036>",
    "GRANDMASTER": "<:grandmaster:1520785144510677022>",
    "CHALLENGER":  "<:challenger:1520784388076601404>",
    "UNRANKED":    "<:unranked:1520787887853928548>",
}

ROLE_EMOJI: dict[str, str] = {
    "탑":   "<:positiontop:1520784540896067785>",
    "정글": "<:positionjungle:1520784513238831187>",
    "미드": "<:positionmiddle:1520784528082337832>",
    "원딜": "<:positionbottom:1520784499078594560>",
    "서폿": "<:positionutility:1520784553340440576>",
    "무관": "<:iconpositionfill:1520789384184725556>",
}

ROLE_KO: dict[str, str] = {
    "TOP": "탑",
    "JUNGLE": "정글",
    "MIDDLE": "미드",
    "BOTTOM": "원딜",
    "UTILITY": "서폿",
}

# 티어별 밸런싱 점수 (팀 구성 알고리즘용)
_TIER_BASE: dict[str, int] = {
    "IRON": 0, "BRONZE": 400, "SILVER": 800,
    "GOLD": 1200, "PLATINUM": 1600, "EMERALD": 2000,
    "DIAMOND": 2400, "MASTER": 2900, "GRANDMASTER": 3000,
    "CHALLENGER": 3100, "UNRANKED": 0,
}
_RANK_BONUS: dict[str, int] = {"IV": 100, "III": 200, "II": 300, "I": 400}


def get_tier_score(tier: str, rank: Optional[str] = None) -> int:
    base = _TIER_BASE.get(tier, 0)
    bonus = _RANK_BONUS.get(rank or "", 0)
    return base + bonus


def format_tier(tier: str, rank: Optional[str] = None, lp: int = 0) -> str:
    emoji = TIER_EMOJI.get(tier, "❓")
    tier_ko = TIER_KO.get(tier, tier)
    if tier in ("MASTER", "GRANDMASTER", "CHALLENGER"):
        return f"{emoji} {tier_ko} {lp}LP"
    if tier == "UNRANKED":
        return f"{emoji} 언랭크"
    return f"{emoji} {tier_ko} {rank}"


def format_tier_short(tier: str, rank: Optional[str] = None) -> str:
    """임베드 한 줄용 축약형"""
    tier_ko = TIER_KO.get(tier, tier)
    if tier in ("MASTER", "GRANDMASTER", "CHALLENGER"):
        return tier_ko
    if tier == "UNRANKED":
        return "언랭"
    return f"{tier_ko} {rank}"


# ── Riot API 호출 ──────────────────────────────────────────────────────────────

async def _get(url: str, **params) -> Optional[dict | list]:
    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=_HEADERS, params=params or None) as resp:
            if resp.status == 200:
                return await resp.json()
            return None


async def get_account_by_riot_id(game_name: str, tag_line: str) -> Optional[dict]:
    return await _get(
        f"https://{RIOT_REGIONAL}.api.riotgames.com"
        f"/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
    )


async def get_ranked_stats_by_puuid(puuid: str) -> list:
    """PUUID로 직접 랭크 조회 — 소환사 ID 불필요"""
    result = await _get(
        f"https://{RIOT_REGION}.api.riotgames.com"
        f"/lol/league/v4/entries/by-puuid/{puuid}"
    )
    return result if isinstance(result, list) else []


async def check_api_status() -> bool:
    """Riot API 응답 여부 확인 (봇 상태 커맨드용)"""
    result = await _get(
        f"https://{RIOT_REGIONAL}.api.riotgames.com/riot/account/v1/me"
    )
    # 401은 인증 오류지만 API 자체는 살아있음
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"https://{RIOT_REGIONAL}.api.riotgames.com/riot/account/v1/me",
            headers=_HEADERS,
        ) as resp:
            return resp.status in (200, 401, 403)


async def _get_main_roles(puuid: str) -> tuple[str, str]:
    match_ids = await _get(
        f"https://{RIOT_REGIONAL}.api.riotgames.com"
        f"/lol/match/v5/matches/by-puuid/{puuid}/ids",
        type="ranked", start=0, count=20,
    )
    if not match_ids:
        return "무관", "무관"

    role_counts: dict[str, int] = {}
    async with aiohttp.ClientSession() as session:
        for match_id in match_ids[:10]:
            async with session.get(
                f"https://{RIOT_REGIONAL}.api.riotgames.com/lol/match/v5/matches/{match_id}",
                headers=_HEADERS,
            ) as resp:
                if resp.status != 200:
                    continue
                match = await resp.json()
            for p in match.get("info", {}).get("participants", []):
                if p.get("puuid") == puuid:
                    pos = p.get("teamPosition", "")
                    if pos:
                        role_counts[pos] = role_counts.get(pos, 0) + 1
                    break

    if not role_counts:
        return "무관", "무관"

    sorted_roles = sorted(role_counts.items(), key=lambda x: x[1], reverse=True)
    main = ROLE_KO.get(sorted_roles[0][0], sorted_roles[0][0])
    sub = ROLE_KO.get(sorted_roles[1][0], sorted_roles[1][0]) if len(sorted_roles) > 1 else "무관"
    return main, sub


async def fetch_full_user_data(game_name: str, tag_line: str) -> Optional[dict]:
    account = await get_account_by_riot_id(game_name, tag_line)
    if not account:
        return None

    puuid: str = account["puuid"]
    ranked = await get_ranked_stats_by_puuid(puuid)
    main_role, sub_role = await _get_main_roles(puuid)

    solo_tier, solo_rank, solo_lp = "UNRANKED", None, 0
    flex_tier, flex_rank, flex_lp = "UNRANKED", None, 0

    for entry in ranked:
        if entry["queueType"] == "RANKED_SOLO_5x5":
            solo_tier, solo_rank, solo_lp = entry["tier"], entry["rank"], entry["leaguePoints"]
        elif entry["queueType"] == "RANKED_FLEX_SR":
            flex_tier, flex_rank, flex_lp = entry["tier"], entry["rank"], entry["leaguePoints"]

    return {
        "puuid": puuid,
        "game_name": game_name,
        "tag_line": tag_line,
        "main_role": main_role,
        "sub_role": sub_role,
        "solo_tier": solo_tier,
        "solo_rank": solo_rank,
        "solo_lp": solo_lp,
        "flex_tier": flex_tier,
        "flex_rank": flex_rank,
        "flex_lp": flex_lp,
    }
