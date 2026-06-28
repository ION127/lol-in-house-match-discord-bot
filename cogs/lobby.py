import re
import discord
from discord import app_commands
from discord.ext import commands
from datetime import datetime, timedelta, timezone
from typing import Optional

import database as db
import riot_api as riot
from config import LOBBY_MAX_MEMBERS


# ── UI 헬퍼 ───────────────────────────────────────────────────────────────────

def _progress_bar(current: int, total: int, length: int = 10) -> str:
    filled = round(length * current / total)
    return "`" + "▓" * filled + "░" * (length - filled) + f"` {current}/{total}명"


def _member_line(idx: int, member: dict) -> str:
    mention = f"<@{member['discord_id']}>"
    pos = member.get("position", "무관")
    pos_icon = riot.ROLE_EMOJI.get(pos, "🎲")

    if member.get("game_name"):
        riot_id = f"{member['game_name']}#{member['tag_line']}"
        tier_short = riot.format_tier_short(
            member.get("solo_tier", "UNRANKED"), member.get("solo_rank")
        )
        tier_emoji = riot.TIER_EMOJI.get(member.get("solo_tier", "UNRANKED"), "❓")
        return (
            f"`{idx:>2}.` {pos_icon} {mention} | "
            f"{tier_emoji} {tier_short} | **{riot_id}**"
        )
    return f"`{idx:>2}.` {pos_icon} {mention} | ❓ 라이엇 ID 미등록"


def build_lobby_embed(lobby: dict, members: list[dict]) -> discord.Embed:
    count = len(members)
    max_m = LOBBY_MAX_MEMBERS
    is_full = count >= max_m

    embed = discord.Embed(
        title="🎮 내전 모집 완료!" if is_full else "🎮 내전 모집 중!",
        color=discord.Color.green() if is_full else discord.Color.blue(),
    )
    embed.description = (
        "모집이 완료되었습니다! 🎉"
        if is_full
        else f"<@{lobby['creator_discord_id']}>님이 내전 맴버를 모집합니다!"
    )

    embed.add_field(
        name=f"참가자 현황",
        value=_progress_bar(count, max_m),
        inline=False,
    )

    # 포지션 현황 요약 (중복 허용, 선착순)
    if members:
        pos_order = ["탑", "정글", "미드", "원딜", "서폿", "무관"]
        pos_counts: dict[str, int] = {}
        for m in members:
            p = m.get("position", "무관")
            pos_counts[p] = pos_counts.get(p, 0) + 1
        parts = [
            f"{riot.ROLE_EMOJI.get(p, '🎲')} {p} {pos_counts[p]}명"
            for p in pos_order
            if pos_counts.get(p, 0) > 0
        ]
        if parts:
            embed.add_field(
                name="포지션 현황",
                value=" · ".join(parts),
                inline=False,
            )

    # 팀별 참가자 목록
    team_groups: dict[str, list[dict]] = {"1팀": [], "2팀": [], "미정": []}
    for m in members:
        t = m.get("team", "미정")
        team_groups.setdefault(t, team_groups["미정"]).append(m)

    idx = 1
    for key, label in [("1팀", "🔵 1팀"), ("2팀", "🔴 2팀")]:
        grp = team_groups[key]
        if grp:
            lines = [_member_line(idx + i, m) for i, m in enumerate(grp)]
            idx += len(grp)
            embed.add_field(name=f"{label} ({len(grp)}명)", value="\n".join(lines), inline=True)
        else:
            embed.add_field(name=f"{label} (0명)", value="없음", inline=True)

    unassigned = team_groups["미정"]
    if unassigned:
        lines = [_member_line(idx + i, m) for i, m in enumerate(unassigned)]
        embed.add_field(
            name=f"⬜ 팀 미정 ({len(unassigned)}명)",
            value="\n".join(lines),
            inline=False,
        )
    elif not members:
        embed.add_field(name="참가자 목록", value="아직 참가자가 없습니다.", inline=False)
    embed.set_footer(text=f"내전 ID: {lobby.get('id', '?')}")
    return embed


def build_completion_embed(lobby: dict, members: list[dict]) -> discord.Embed:
    embed = discord.Embed(
        title="🎉 내전 모집 완료!",
        description=f"<@{lobby['creator_discord_id']}>님의 내전 멤버가 모두 모였습니다!\n​",
        color=discord.Color.gold(),
    )
    team_groups: dict[str, list[dict]] = {"1팀": [], "2팀": [], "미정": []}
    for m in members:
        t = m.get("team", "미정")
        team_groups.setdefault(t, team_groups["미정"]).append(m)

    idx = 1
    for key, label in [("1팀", "🔵 1팀"), ("2팀", "🔴 2팀")]:
        grp = team_groups[key]
        if grp:
            lines = [_member_line(idx + i, m) for i, m in enumerate(grp)]
            idx += len(grp)
            embed.add_field(name=f"{label} ({len(grp)}명)", value="\n".join(lines), inline=True)

    unassigned = team_groups["미정"]
    if unassigned:
        lines = [_member_line(idx + i, m) for i, m in enumerate(unassigned)]
        embed.add_field(name=f"⬜ 팀 미정 ({len(unassigned)}명)", value="\n".join(lines), inline=False)

    return embed


# ── 임베드 갱신 ───────────────────────────────────────────────────────────────

async def refresh_lobby_embed(bot: commands.Bot, lobby_id: int) -> None:
    lobby = await db.get_lobby(lobby_id)
    if not lobby or not lobby.get("message_id"):
        return

    members = await db.get_lobby_members(lobby_id)
    count = len(members)

    settings = await db.get_server_settings(lobby.get("guild_id", ""))
    max_m = settings.get("max_members", LOBBY_MAX_MEMBERS)

    try:
        channel = bot.get_channel(int(lobby["channel_id"])) or await bot.fetch_channel(
            int(lobby["channel_id"])
        )
        message = await channel.fetch_message(int(lobby["message_id"]))
    except Exception as e:
        print(f"[lobby] 메시지 갱신 실패: {e}")
        return

    if count >= max_m and lobby["status"] == "open":
        await db.close_lobby(lobby_id, "full")
        await db.log_event("lobby_full", guild_id=lobby.get("guild_id"), lobby_id=lobby_id)
        await message.edit(embed=build_lobby_embed(lobby, members), view=None)
        await channel.send(embed=build_completion_embed(lobby, members))

        # 참가자 전원 DM
        for member in members:
            await _try_dm(
                bot,
                int(member["discord_id"]),
                f"🎮 **내전 모집 완료!**\n"
                f"<@{lobby['creator_discord_id']}>님의 내전에 참가자가 모두 모였습니다!\n"
                f"채널을 확인해주세요.",
            )
    else:
        view = LobbyView(lobby_id, bot)
        await message.edit(embed=build_lobby_embed(lobby, members), view=view)


async def _try_dm(bot: commands.Bot, user_id: int, content: str) -> None:
    try:
        user = bot.get_user(user_id) or await bot.fetch_user(user_id)
        await user.send(content)
    except Exception:
        pass  # DM 비활성화 유저는 무시


# ── 포지션 선택 뷰 (ephemeral) ────────────────────────────────────────────────

class PositionSelectView(discord.ui.View):
    def __init__(self, lobby_id: int, bot: commands.Bot):
        super().__init__(timeout=60)
        self.lobby_id = lobby_id
        self.bot = bot

    @discord.ui.select(
        placeholder="참가할 포지션을 선택하세요",
        options=[
            discord.SelectOption(label="탑",   value="탑",   emoji=discord.PartialEmoji(name="positiontop",     id=1520784540896067785)),
            discord.SelectOption(label="정글", value="정글", emoji=discord.PartialEmoji(name="positionjungle",  id=1520784513238831187)),
            discord.SelectOption(label="미드", value="미드", emoji=discord.PartialEmoji(name="positionmiddle",  id=1520784528082337832)),
            discord.SelectOption(label="원딜", value="원딜", emoji=discord.PartialEmoji(name="positionbottom",  id=1520784499078594560)),
            discord.SelectOption(label="서폿", value="서폿", emoji=discord.PartialEmoji(name="positionutility", id=1520784553340440576)),
            discord.SelectOption(label="무관", value="무관", emoji=discord.PartialEmoji(name="iconpositionfill",id=1520789384184725556)),
        ],
    )
    async def select_position(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ) -> None:
        position = select.values[0]
        pos_icon = riot.ROLE_EMOJI.get(position, "🎲")
        await interaction.response.edit_message(
            content=f"{pos_icon} **{position}** 선택! 참가할 팀을 선택해주세요:",
            view=TeamSelectView(self.lobby_id, self.bot, position),
        )

    async def on_timeout(self) -> None:
        self.select_position.disabled = True


# ── 팀 선택 뷰 ───────────────────────────────────────────────────────────────

class TeamSelectView(discord.ui.View):
    def __init__(self, lobby_id: int, bot: commands.Bot, position: str):
        super().__init__(timeout=60)
        self.lobby_id = lobby_id
        self.bot = bot
        self.position = position

    @discord.ui.select(
        placeholder="참가할 팀을 선택하세요",
        options=[
            discord.SelectOption(label="1팀", value="1팀", emoji="🔵"),
            discord.SelectOption(label="2팀", value="2팀", emoji="🔴"),
        ],
    )
    async def select_team(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ) -> None:
        team = select.values[0]
        user = await db.get_user(str(interaction.user.id))

        if not user or not user.get("game_name"):
            await interaction.response.send_modal(
                RiotIDModal(self.lobby_id, self.bot, position=self.position, team=team)
            )
            return

        added = await db.add_lobby_member(
            self.lobby_id, str(interaction.user.id), position=self.position, team=team
        )
        if not added:
            await interaction.response.edit_message(content="이미 내전에 참가 중입니다.", view=None)
            return

        await db.log_event(
            "member_joined",
            lobby_id=self.lobby_id,
            discord_id=str(interaction.user.id),
            detail=f"{self.position}/{team}",
        )
        count = await db.get_lobby_member_count(self.lobby_id)
        solo_str = riot.format_tier(
            user.get("solo_tier", "UNRANKED"), user.get("solo_rank"), user.get("solo_lp", 0)
        )
        pos_icon = riot.ROLE_EMOJI.get(self.position, "🎲")
        team_emoji = "🔵" if team == "1팀" else "🔴"
        await interaction.response.edit_message(
            content=(
                f"✅ {team_emoji} **{team}** | {pos_icon} **{self.position}** 포지션으로 참가했습니다!\n"
                f"**{user['game_name']}#{user['tag_line']}** | 솔로: {solo_str}\n"
                f"현재 참가자: **{count}/{LOBBY_MAX_MEMBERS}명**"
            ),
            view=None,
        )
        await refresh_lobby_embed(self.bot, self.lobby_id)

    async def on_timeout(self) -> None:
        self.select_team.disabled = True


# ── 라이엇 ID 등록 모달 ────────────────────────────────────────────────────────

class RiotIDModal(discord.ui.Modal, title="라이엇 계정 연동"):
    game_name_input = discord.ui.TextInput(
        label="게임 이름",
        placeholder="예: Hide on bush",
        max_length=50,
        required=True,
    )
    tag_line_input = discord.ui.TextInput(
        label="태그라인",
        placeholder="# 없이 입력 (예: KR1)",
        max_length=10,
        required=True,
    )

    def __init__(self, lobby_id: int, bot: commands.Bot, position: str = "무관", team: str = "미정"):
        super().__init__()
        self.lobby_id = lobby_id
        self.bot = bot
        self.position = position
        self.team = team

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        game_name = self.game_name_input.value.strip()
        tag_line = self.tag_line_input.value.strip().lstrip("#")

        await interaction.followup.send(
            f"⏳ `{game_name}#{tag_line}` 계정 정보를 가져오는 중입니다...",
            ephemeral=True,
        )

        data = await riot.fetch_full_user_data(game_name, tag_line)
        if not data:
            await interaction.followup.send(
                f"❌ `{game_name}#{tag_line}` 계정을 찾을 수 없습니다.\n"
                "게임 이름과 태그를 다시 확인해주세요.",
                ephemeral=True,
            )
            return

        await db.upsert_user(str(interaction.user.id), **data)

        lobby = await db.get_lobby(self.lobby_id)
        if not lobby or lobby["status"] != "open":
            await interaction.followup.send(
                "✅ 계정 연동 완료! (내전이 이미 종료되었습니다.)", ephemeral=True
            )
            return

        added = await db.add_lobby_member(
            self.lobby_id, str(interaction.user.id), position=self.position, team=self.team
        )
        if not added:
            await interaction.followup.send("이미 내전에 참가 중입니다.", ephemeral=True)
            return

        await db.log_event(
            "member_joined",
            lobby_id=self.lobby_id,
            discord_id=str(interaction.user.id),
            detail=f"{self.position}/{self.team} (신규등록)",
        )
        count = await db.get_lobby_member_count(self.lobby_id)
        solo_str = riot.format_tier(data["solo_tier"], data.get("solo_rank"), data.get("solo_lp", 0))
        flex_str = riot.format_tier(data["flex_tier"], data.get("flex_rank"), data.get("flex_lp", 0))
        pos_icon = riot.ROLE_EMOJI.get(self.position, "🎲")
        team_emoji = "🔵" if self.team == "1팀" else "🔴" if self.team == "2팀" else "⬜"

        await interaction.followup.send(
            f"✅ 계정 연동 완료 & 내전 참가!\n"
            f"**{game_name}#{tag_line}**\n"
            f"주라인: {data['main_role']} | 부라인: {data['sub_role']}\n"
            f"솔로랭크: {solo_str} | 자유랭크: {flex_str}\n\n"
            f"팀: {team_emoji} **{self.team}** | 포지션: {pos_icon} **{self.position}** | 현재 **{count}/{LOBBY_MAX_MEMBERS}명**",
            ephemeral=True,
        )
        await refresh_lobby_embed(self.bot, self.lobby_id)

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        import traceback
        traceback.print_exc()
        print(f"[RiotIDModal] 오류 타입: {type(error).__name__}, 내용: {error}")
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ 오류가 발생했습니다. 잠시 후 다시 시도해주세요.", ephemeral=True
            )


# ── 내전 버튼 뷰 (Persistent) ─────────────────────────────────────────────────

class LobbyView(discord.ui.View):
    def __init__(self, lobby_id: int, bot: commands.Bot):
        super().__init__(timeout=None)
        self.lobby_id = lobby_id
        self.bot = bot

        join_btn = discord.ui.Button(
            label="✅ 참가하기",
            style=discord.ButtonStyle.green,
            custom_id=f"join_lobby_{lobby_id}",
        )
        join_btn.callback = self._join

        leave_btn = discord.ui.Button(
            label="❌ 나가기",
            style=discord.ButtonStyle.red,
            custom_id=f"leave_lobby_{lobby_id}",
        )
        leave_btn.callback = self._leave

        self.add_item(join_btn)
        self.add_item(leave_btn)

    async def _join(self, interaction: discord.Interaction) -> None:
        lobby = await db.get_lobby(self.lobby_id)
        if not lobby or lobby["status"] != "open":
            await interaction.response.send_message("❌ 내전이 마감되었습니다.", ephemeral=True)
            return

        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        banned, reason = await db.is_banned(guild_id, str(interaction.user.id))
        if banned:
            await interaction.response.send_message(
                f"❌ 내전 참가가 금지되어 있습니다.\n사유: {reason or '없음'}", ephemeral=True
            )
            return

        already = await db.is_member_in_lobby(self.lobby_id, str(interaction.user.id))
        if already:
            await interaction.response.send_message("이미 내전에 참가 중입니다.", ephemeral=True)
            return

        settings = await db.get_server_settings(guild_id)
        max_m = settings.get("max_members", LOBBY_MAX_MEMBERS)
        count = await db.get_lobby_member_count(self.lobby_id)

        if count >= max_m:
            # 대기열 처리
            wl_pos = await db.get_waitlist_position(self.lobby_id, str(interaction.user.id))
            if wl_pos > 0:
                await interaction.response.send_message(
                    f"이미 대기열에 등록되어 있습니다. (대기 {wl_pos}번)", ephemeral=True
                )
                return

            # 대기열 추가를 위한 포지션 선택
            await interaction.response.send_message(
                "⏳ 내전이 가득 찼습니다. 희망 포지션을 선택하면 대기열에 등록됩니다.",
                view=WaitlistPositionSelectView(self.lobby_id, self.bot),
                ephemeral=True,
            )
            return

        # 자리 있음 → Riot 데이터 갱신 후 포지션 선택
        # API 호출이 있어서 defer 후 followup으로 처리
        await interaction.response.defer(ephemeral=True)

        user = await db.get_user(str(interaction.user.id))
        if user and user.get("game_name"):
            refreshed = await riot.fetch_full_user_data(user["game_name"], user["tag_line"])
            if refreshed:
                await db.upsert_user(str(interaction.user.id), **refreshed)

        await interaction.followup.send(
            "참가할 포지션을 선택해주세요:",
            view=PositionSelectView(self.lobby_id, self.bot),
            ephemeral=True,
        )

    async def _leave(self, interaction: discord.Interaction) -> None:
        lobby = await db.get_lobby(self.lobby_id)
        if not lobby or lobby["status"] != "open":
            await interaction.response.send_message("❌ 내전이 마감되었습니다.", ephemeral=True)
            return

        if lobby["creator_discord_id"] == str(interaction.user.id):
            await interaction.response.send_message(
                "❌ 내전 개설자는 나갈 수 없습니다.\n`/내전취소`로 내전을 취소할 수 있습니다.",
                ephemeral=True,
            )
            return

        removed = await db.remove_lobby_member(self.lobby_id, str(interaction.user.id))
        if not removed:
            await interaction.response.send_message(
                "내전에 참가하고 있지 않습니다.", ephemeral=True
            )
            return

        await db.log_event("member_left", lobby_id=self.lobby_id, discord_id=str(interaction.user.id))
        count = await db.get_lobby_member_count(self.lobby_id)
        await interaction.response.send_message(
            f"내전에서 나갔습니다. (현재 {count}/{LOBBY_MAX_MEMBERS}명)", ephemeral=True
        )

        # 대기열 첫 번째 자동 승격
        first = await db.pop_first_waitlist(self.lobby_id)
        if first:
            added = await db.add_lobby_member(
                self.lobby_id, first["discord_id"],
                position=first["position"], team=first.get("team", "미정"),
            )
            if added:
                await db.log_event("member_promoted", lobby_id=self.lobby_id, discord_id=first["discord_id"])
                pos_icon = riot.ROLE_EMOJI.get(first["position"], "🎲")
                team_emoji = "🔵" if first.get("team") == "1팀" else "🔴" if first.get("team") == "2팀" else "⬜"
                await _try_dm(
                    self.bot,
                    int(first["discord_id"]),
                    f"🎮 **내전 자리가 생겼습니다!**\n"
                    f"대기 중이던 내전에 자동으로 참가되었습니다.\n"
                    f"팀: {team_emoji} {first.get('team', '미정')} | 포지션: {pos_icon} {first['position']}",
                )

        await refresh_lobby_embed(self.bot, self.lobby_id)


# ── 대기열 포지션 선택 뷰 ─────────────────────────────────────────────────────

class WaitlistPositionSelectView(discord.ui.View):
    def __init__(self, lobby_id: int, bot: commands.Bot):
        super().__init__(timeout=60)
        self.lobby_id = lobby_id
        self.bot = bot

    @discord.ui.select(
        placeholder="희망 포지션을 선택하세요 (대기열용)",
        options=[
            discord.SelectOption(label="탑",   value="탑",   emoji=discord.PartialEmoji(name="positiontop",     id=1520784540896067785)),
            discord.SelectOption(label="정글", value="정글", emoji=discord.PartialEmoji(name="positionjungle",  id=1520784513238831187)),
            discord.SelectOption(label="미드", value="미드", emoji=discord.PartialEmoji(name="positionmiddle",  id=1520784528082337832)),
            discord.SelectOption(label="원딜", value="원딜", emoji=discord.PartialEmoji(name="positionbottom",  id=1520784499078594560)),
            discord.SelectOption(label="서폿", value="서폿", emoji=discord.PartialEmoji(name="positionutility", id=1520784553340440576)),
            discord.SelectOption(label="무관", value="무관", emoji=discord.PartialEmoji(name="iconpositionfill",id=1520789384184725556)),
        ],
    )
    async def select_waitlist_position(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ) -> None:
        position = select.values[0]
        pos_icon = riot.ROLE_EMOJI.get(position, "🎲")
        await interaction.response.edit_message(
            content=f"{pos_icon} **{position}** 선택! 선호 팀을 선택해주세요:",
            view=WaitlistTeamSelectView(self.lobby_id, self.bot, position),
        )


# ── 대기열 팀 선택 뷰 ────────────────────────────────────────────────────────

class WaitlistTeamSelectView(discord.ui.View):
    def __init__(self, lobby_id: int, bot: commands.Bot, position: str):
        super().__init__(timeout=60)
        self.lobby_id = lobby_id
        self.bot = bot
        self.position = position

    @discord.ui.select(
        placeholder="선호 팀을 선택하세요 (대기열용)",
        options=[
            discord.SelectOption(label="1팀", value="1팀", emoji="🔵"),
            discord.SelectOption(label="2팀", value="2팀", emoji="🔴"),
        ],
    )
    async def select_waitlist_team(
        self, interaction: discord.Interaction, select: discord.ui.Select
    ) -> None:
        team = select.values[0]
        added = await db.add_to_waitlist(
            self.lobby_id, str(interaction.user.id), position=self.position, team=team
        )
        if not added:
            await interaction.response.edit_message(content="이미 대기열에 등록되어 있습니다.", view=None)
            return

        waitlist = await db.get_waitlist(self.lobby_id)
        wl_pos = next(
            (i + 1 for i, e in enumerate(waitlist) if e["discord_id"] == str(interaction.user.id)),
            len(waitlist),
        )
        pos_icon = riot.ROLE_EMOJI.get(self.position, "🎲")
        team_emoji = "🔵" if team == "1팀" else "🔴"
        await interaction.response.edit_message(
            content=(
                f"⏳ 대기열에 등록되었습니다!\n"
                f"대기 순번: **{wl_pos}번** | 팀: {team_emoji} **{team}** | 포지션: {pos_icon} **{self.position}**\n"
                f"자리가 생기면 자동으로 참가 처리되고 DM으로 알림을 보내드립니다."
            ),
            view=None,
        )


# ── Cog ───────────────────────────────────────────────────────────────────────

def _parse_time_seconds(s: str) -> int:
    """'30분', '1시간', '1h30m' 등을 초로 변환"""
    total = 0
    for match in re.finditer(r"(\d+)\s*(h|시간|m|분)", s):
        val = int(match.group(1))
        unit = match.group(2)
        total += val * 3600 if unit in ("h", "시간") else val * 60
    return total


class LobbyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _send_to_log(self, guild_id: str, content: str) -> None:
        settings = await db.get_server_settings(guild_id)
        log_ch_id = settings.get("log_channel_id")
        if not log_ch_id:
            return
        try:
            ch = self.bot.get_channel(int(log_ch_id)) or await self.bot.fetch_channel(int(log_ch_id))
            await ch.send(content)
        except Exception:
            pass

    @app_commands.command(name="내전시작", description="새로운 내전을 개설합니다.")
    async def start_lobby(self, interaction: discord.Interaction) -> None:
        # Discord는 3초 안에 응답하지 않으면 interaction을 만료시키므로 즉시 defer
        await interaction.response.defer(ephemeral=True)

        creator_id = str(interaction.user.id)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""

        # 기존 내전 확인
        existing = await db.get_active_lobby_by_creator(creator_id)
        if existing:
            await interaction.followup.send(
                "❌ 이미 개설한 내전이 있습니다.\n"
                "`/내전취소`로 기존 내전을 취소한 뒤 새로 만들 수 있습니다.",
                ephemeral=True,
            )
            return

        # 쿨타임 확인
        settings = await db.get_server_settings(guild_id)
        cooldown_min = settings.get("cooldown_minutes", 5)
        remaining = await db.get_cooldown_remaining(creator_id, cooldown_min)
        if remaining > 0:
            mins, secs = divmod(remaining, 60)
            await interaction.followup.send(
                f"❌ 내전 취소 후 재개설 쿨타임 중입니다.\n"
                f"남은 시간: **{mins}분 {secs}초**",
                ephemeral=True,
            )
            return

        # 차단 여부 확인
        banned, reason = await db.is_banned(guild_id, creator_id)
        if banned:
            await interaction.followup.send(
                f"❌ 내전 개설이 금지되어 있습니다.\n사유: {reason or '없음'}", ephemeral=True
            )
            return

        lobby_id = await db.create_lobby(creator_id, str(interaction.channel_id), guild_id)
        lobby = await db.get_lobby(lobby_id)
        await db.log_event("lobby_created", guild_id=guild_id, lobby_id=lobby_id, discord_id=creator_id)

        view = LobbyView(lobby_id, self.bot)
        embed = build_lobby_embed(lobby, [])

        # 알림 역할 태그
        alert_role_id = settings.get("alert_role_id")
        content = f"<@{creator_id}>님이 내전 맴버를 모집합니다."
        if alert_role_id:
            content = f"<@&{alert_role_id}> {content}"

        # 공개 메시지는 channel.send()로 직접 전송, ephemeral defer 응답은 삭제
        msg = await interaction.channel.send(content=content, embed=embed, view=view)
        await db.update_lobby_message(lobby_id, str(msg.id))
        await interaction.delete_original_response()

        # 개설자도 포지션·팀 선택 후 참가 (다른 참가자와 동일한 흐름)
        user = await db.get_user(creator_id)
        if user and user.get("game_name"):
            await interaction.followup.send(
                "✅ 내전이 개설되었습니다!\n참가할 포지션을 선택해주세요:",
                view=PositionSelectView(lobby_id, self.bot),
                ephemeral=True,
            )
        else:
            await interaction.followup.send(
                "✅ 내전이 개설되었습니다!\n"
                "⚠️ 라이엇 계정이 등록되어 있지 않습니다.\n"
                "**참가하기** 버튼을 눌러 계정을 등록하고 내전에 참가하세요.",
                ephemeral=True,
            )

        await self._send_to_log(
            guild_id, f"📋 [내전 개설] <@{creator_id}> | 내전 ID: {lobby_id}"
        )

    @app_commands.command(name="내전취소", description="개설한 내전을 취소합니다.")
    async def cancel_lobby(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        creator_id = str(interaction.user.id)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        lobby = await db.get_active_lobby_by_creator(creator_id)

        if not lobby:
            await interaction.followup.send("❌ 개설된 내전이 없습니다.", ephemeral=True)
            return

        members = await db.get_lobby_members(lobby["id"])
        await db.close_lobby(lobby["id"], "cancelled")
        await db.set_cooldown(creator_id)
        await db.log_event("lobby_cancelled", guild_id=guild_id, lobby_id=lobby["id"], discord_id=creator_id)

        # 모집 메시지 수정
        try:
            ch = self.bot.get_channel(int(lobby["channel_id"])) or await self.bot.fetch_channel(
                int(lobby["channel_id"])
            )
            if lobby.get("message_id"):
                msg = await ch.fetch_message(int(lobby["message_id"]))
                cancel_embed = discord.Embed(
                    title="❌ 내전 취소됨",
                    description="내전이 취소되었습니다.",
                    color=discord.Color.red(),
                )
                await msg.edit(embed=cancel_embed, view=None)
        except Exception as e:
            print(f"[lobby] 취소 메시지 수정 실패: {e}")

        await interaction.followup.send("✅ 내전이 취소되었습니다.", ephemeral=True)

        # 참가자 전원 DM
        for member in members:
            if member["discord_id"] == creator_id:
                continue
            await _try_dm(
                self.bot,
                int(member["discord_id"]),
                f"❌ **내전이 취소되었습니다.**\n"
                f"내전 ID: `#{lobby['id']}` | 개설자: <@{creator_id}> | 채널: <#{lobby['channel_id']}>",
            )

        # 대기열 멤버에게도 DM
        waitlist = await db.get_waitlist(lobby["id"])
        for entry in waitlist:
            await _try_dm(
                self.bot,
                int(entry["discord_id"]),
                f"❌ **대기 중이던 내전이 취소되었습니다.**\n"
                f"내전 ID: `#{lobby['id']}` | 개설자: <@{creator_id}> | 채널: <#{lobby['channel_id']}>",
            )

        await self._send_to_log(
            guild_id, f"🗑️ [내전 취소] <@{creator_id}> | 내전 ID: {lobby['id']}"
        )

    @app_commands.command(name="내전예약", description="일정 시간 후 내전을 자동으로 개설합니다.")
    @app_commands.describe(time="예약 시간 (예: 30분, 1시간, 1시간30분)")
    async def schedule_lobby(self, interaction: discord.Interaction, time: str) -> None:
        await interaction.response.defer(ephemeral=True)

        creator_id = str(interaction.user.id)
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""

        seconds = _parse_time_seconds(time)
        if seconds <= 0:
            await interaction.followup.send(
                "❌ 올바른 시간 형식을 입력하세요. (예: `30분`, `1시간`, `1시간30분`)",
                ephemeral=True,
            )
            return
        if seconds > 86400:
            await interaction.followup.send(
                "❌ 예약은 최대 24시간까지 가능합니다.", ephemeral=True
            )
            return

        fire_at = (datetime.now(timezone.utc) + timedelta(seconds=seconds)).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
        await db.add_scheduled_lobby(creator_id, guild_id, str(interaction.channel_id), fire_at)

        mins = seconds // 60
        await interaction.followup.send(
            f"⏰ **{mins}분** 후 내전이 자동으로 개설됩니다!", ephemeral=True
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LobbyCog(bot))
