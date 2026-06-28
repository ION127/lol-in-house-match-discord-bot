import discord
from discord import app_commands
from discord.ext import commands
from typing import Optional

import database as db
import riot_api as riot

POSITIONS = ["탑", "정글", "미드", "원딜", "서폿"]


# ── 팀 구성 알고리즘 ──────────────────────────────────────────────────────────

def _snake_draft(members: list[dict]) -> tuple[list[dict], list[dict]]:
    """티어 점수 내림차순 정렬 후 뱀 드래프트로 두 팀 분배"""
    sorted_members = sorted(
        members,
        key=lambda m: riot.get_tier_score(
            m.get("solo_tier", "UNRANKED"), m.get("solo_rank")
        ),
        reverse=True,
    )
    # 뱀 드래프트: blue=1,4,5,8,9 / red=2,3,6,7,10 (0-indexed: 0,3,4,7,8 / 1,2,5,6,9)
    blue_indices = {0, 3, 4, 7, 8}
    blue, red = [], []
    for i, m in enumerate(sorted_members):
        (blue if i in blue_indices else red).append(m)
    return blue, red


def _assign_positions(team: list[dict]) -> dict[str, str]:
    """선착순(joined_at) 기준 포지션 배정.
    같은 포지션이 겹치면 먼저 참가한 사람이 우선권을 가진다.
    무관 or 밀린 사람은 남은 포지션으로 순서대로 배정."""
    available = list(POSITIONS)
    result: dict[str, str] = {}
    flex_queue: list[dict] = []

    # joined_at 선착순 정렬
    sorted_team = sorted(team, key=lambda m: m.get("joined_at", ""))

    for m in sorted_team:
        chosen = m.get("position", "무관")
        if chosen != "무관" and chosen in available:
            result[m["discord_id"]] = chosen
            available.remove(chosen)
        else:
            flex_queue.append(m)

    for m in flex_queue:
        if available:
            result[m["discord_id"]] = available.pop(0)
        else:
            result[m["discord_id"]] = "무관"

    return result


def _avg_score(team: list[dict]) -> int:
    if not team:
        return 0
    return sum(
        riot.get_tier_score(m.get("solo_tier", "UNRANKED"), m.get("solo_rank"))
        for m in team
    ) // len(team)


def _score_to_tier_str(score: int) -> str:
    """점수를 티어 문자열로 변환 (임베드 표시용)"""
    thresholds = [
        (2900, "마스터+"),
        (2500, "다이아"),
        (2100, "에메랄드"),
        (1700, "플래티넘"),
        (1300, "골드"),
        (900, "실버"),
        (500, "브론즈"),
        (100, "아이언"),
    ]
    for threshold, label in thresholds:
        if score >= threshold:
            return label
    return "언랭"


def _build_team_embed(
    blue: list[dict],
    red: list[dict],
    blue_pos: dict[str, str],
    red_pos: dict[str, str],
    lobby_id: int,
) -> discord.Embed:
    embed = discord.Embed(title="⚔️ 팀 구성 결과", color=discord.Color.purple())

    def team_lines(team: list[dict], pos_map: dict[str, str]) -> str:
        lines = []
        for m in team:
            pos = pos_map.get(m["discord_id"], "무관")
            chosen = m.get("position", "무관")
            pos_icon = riot.ROLE_EMOJI.get(pos, "🎲")
            tier_short = riot.format_tier_short(
                m.get("solo_tier", "UNRANKED"), m.get("solo_rank")
            )
            tier_emoji = riot.TIER_EMOJI.get(m.get("solo_tier", "UNRANKED"), "❓")
            mention = f"<@{m['discord_id']}>"
            # 희망 포지션과 배정 포지션이 다르면 취소선으로 표시
            if chosen not in ("무관", pos):
                pos_display = f"~~{chosen}~~→**{pos}**"
            else:
                pos_display = f"**{pos}**"
            if m.get("game_name"):
                lines.append(
                    f"{pos_icon} {pos_display} | {mention} | {tier_emoji} {tier_short}"
                )
            else:
                lines.append(f"{pos_icon} {pos_display} | {mention} | ❓ 언랭")
        return "\n".join(lines) if lines else "없음"

    blue_avg = _score_to_tier_str(_avg_score(blue))
    red_avg = _score_to_tier_str(_avg_score(red))

    embed.add_field(
        name=f"🔵 블루팀  (평균: {blue_avg})",
        value=team_lines(blue, blue_pos),
        inline=True,
    )
    embed.add_field(
        name=f"🔴 레드팀  (평균: {red_avg})",
        value=team_lines(red, red_pos),
        inline=True,
    )
    embed.set_footer(text=f"내전 ID: {lobby_id} | 티어 기반 자동 밸런싱")
    return embed


# ── 다시 짜기 버튼 뷰 ─────────────────────────────────────────────────────────

class TeamView(discord.ui.View):
    def __init__(self, lobby_id: int, members: list[dict]):
        super().__init__(timeout=300)
        self.lobby_id = lobby_id
        self.members = members

    @discord.ui.button(label="🔀 다시 짜기", style=discord.ButtonStyle.secondary)
    async def reshuffle(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        # 새 팀 구성 (단순 재정렬: 동점자 순서 변경 효과)
        import random
        shuffled = list(self.members)

        # 같은 티어 점수끼리 셔플
        from itertools import groupby
        shuffled.sort(
            key=lambda m: riot.get_tier_score(m.get("solo_tier", "UNRANKED"), m.get("solo_rank")),
            reverse=True,
        )
        result = []
        for _, group in groupby(
            shuffled,
            key=lambda m: riot.get_tier_score(m.get("solo_tier", "UNRANKED"), m.get("solo_rank")),
        ):
            g = list(group)
            random.shuffle(g)
            result.extend(g)

        blue, red = _snake_draft(result)
        blue_pos = _assign_positions(blue)
        red_pos = _assign_positions(red)

        embed = _build_team_embed(blue, red, blue_pos, red_pos, self.lobby_id)
        self.members = result
        await interaction.response.edit_message(embed=embed, view=self)


# ── Cog ───────────────────────────────────────────────────────────────────────

class TeamCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="팀섞기",
        description="현재 채널의 완성된 내전을 두 팀으로 자동 편성합니다.",
    )
    @app_commands.describe(lobby_id="내전 ID (생략 시 이 채널의 최근 내전 사용)")
    async def make_teams(
        self, interaction: discord.Interaction, lobby_id: Optional[int] = None
    ) -> None:
        if lobby_id is None:
            # 채널의 최근 완료/진행 중 내전 찾기
            lobby = await _find_lobby_in_channel(str(interaction.channel_id))
        else:
            lobby = await db.get_lobby(lobby_id)

        if not lobby:
            await interaction.response.send_message(
                "❌ 이 채널에 진행 중이거나 완료된 내전이 없습니다.\n"
                "내전 ID를 직접 지정하거나, 내전이 있는 채널에서 사용해주세요.",
                ephemeral=True,
            )
            return

        members = await db.get_lobby_members(lobby["id"])
        if len(members) < 2:
            await interaction.response.send_message(
                "❌ 팀을 짜려면 최소 2명이 필요합니다.", ephemeral=True
            )
            return

        blue, red = _snake_draft(members)
        blue_pos = _assign_positions(blue)
        red_pos = _assign_positions(red)

        embed = _build_team_embed(blue, red, blue_pos, red_pos, lobby["id"])
        view = TeamView(lobby["id"], members)

        await interaction.response.send_message(embed=embed, view=view)


async def _find_lobby_in_channel(channel_id: str) -> Optional[dict]:
    """채널 ID로 가장 최근 open 또는 full 내전 조회"""
    import aiosqlite
    from config import DATABASE_PATH

    async with aiosqlite.connect(DATABASE_PATH) as db_conn:
        db_conn.row_factory = aiosqlite.Row
        async with db_conn.execute(
            """SELECT * FROM lobbies
               WHERE channel_id = ? AND status IN ('open', 'full')
               ORDER BY created_at DESC LIMIT 1""",
            (channel_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TeamCog(bot))
