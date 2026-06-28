import time
import discord
from discord import app_commands
from discord.ext import commands

import database as db
import riot_api as riot


class ProfileCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ── /아이디변경 ────────────────────────────────────────────────────────────

    @app_commands.command(name="아이디변경", description="연동된 라이엇 계정을 변경합니다.")
    @app_commands.describe(
        game_name="라이엇 게임 이름",
        tag_line="태그라인 (# 없이 입력, 예: KR1)",
    )
    async def change_id(
        self, interaction: discord.Interaction, game_name: str, tag_line: str
    ) -> None:
        await interaction.response.defer(ephemeral=True)
        tag_line = tag_line.lstrip("#")

        await interaction.followup.send(
            f"⏳ `{game_name}#{tag_line}` 계정 정보를 가져오는 중...", ephemeral=True
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

        solo_str = riot.format_tier(data["solo_tier"], data.get("solo_rank"), data.get("solo_lp", 0))
        flex_str = riot.format_tier(data["flex_tier"], data.get("flex_rank"), data.get("flex_lp", 0))

        await interaction.followup.send(
            f"✅ 라이엇 계정이 변경되었습니다!\n"
            f"**{game_name}#{tag_line}**\n"
            f"주라인: {riot.ROLE_EMOJI.get(data['main_role'], '')} {data['main_role']} | "
            f"부라인: {riot.ROLE_EMOJI.get(data['sub_role'], '')} {data['sub_role']}\n"
            f"솔로랭크: {solo_str} | 자유랭크: {flex_str}",
            ephemeral=True,
        )

    # ── /갱신 ─────────────────────────────────────────────────────────────────

    @app_commands.command(name="갱신", description="라이엇 API에서 내 티어·라인 정보를 최신화합니다.")
    async def refresh(self, interaction: discord.Interaction) -> None:
        user = await db.get_user(str(interaction.user.id))
        if not user or not user.get("game_name"):
            await interaction.response.send_message(
                "❌ 연동된 라이엇 계정이 없습니다.\n`/아이디변경`으로 먼저 계정을 등록해주세요.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send(
            f"⏳ `{user['game_name']}#{user['tag_line']}` 정보를 갱신하는 중...",
            ephemeral=True,
        )

        data = await riot.fetch_full_user_data(user["game_name"], user["tag_line"])
        if not data:
            await interaction.followup.send(
                "❌ 계정 정보를 가져오지 못했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )
            return

        await db.upsert_user(str(interaction.user.id), **data)

        solo_str = riot.format_tier(data["solo_tier"], data.get("solo_rank"), data.get("solo_lp", 0))
        flex_str = riot.format_tier(data["flex_tier"], data.get("flex_rank"), data.get("flex_lp", 0))

        await interaction.followup.send(
            f"✅ 정보가 갱신되었습니다!\n"
            f"주라인: {riot.ROLE_EMOJI.get(data['main_role'], '')} {data['main_role']} | "
            f"부라인: {riot.ROLE_EMOJI.get(data['sub_role'], '')} {data['sub_role']}\n"
            f"솔로랭크: {solo_str} | 자유랭크: {flex_str}",
            ephemeral=True,
        )

    # ── /내정보 ───────────────────────────────────────────────────────────────

    @app_commands.command(name="내정보", description="연동된 라이엇 계정 정보를 확인합니다.")
    async def my_info(self, interaction: discord.Interaction) -> None:
        user = await db.get_user(str(interaction.user.id))
        if not user or not user.get("game_name"):
            await interaction.response.send_message(
                "❌ 연동된 라이엇 계정이 없습니다.\n`/아이디변경`으로 계정을 등록하세요.",
                ephemeral=True,
            )
            return

        solo_str = riot.format_tier(
            user.get("solo_tier", "UNRANKED"), user.get("solo_rank"), user.get("solo_lp", 0)
        )
        flex_str = riot.format_tier(
            user.get("flex_tier", "UNRANKED"), user.get("flex_rank"), user.get("flex_lp", 0)
        )
        main_icon = riot.ROLE_EMOJI.get(user.get("main_role", "무관"), "🎲")
        sub_icon = riot.ROLE_EMOJI.get(user.get("sub_role", "무관"), "🎲")

        embed = discord.Embed(
            title=f"{interaction.user.display_name}의 롤 정보",
            color=discord.Color.blue(),
        )
        embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(
            name="라이엇 ID",
            value=f"**{user['game_name']}#{user['tag_line']}**",
            inline=False,
        )
        embed.add_field(
            name="주라인", value=f"{main_icon} {user.get('main_role', '무관')}", inline=True
        )
        embed.add_field(
            name="부라인", value=f"{sub_icon} {user.get('sub_role', '무관')}", inline=True
        )
        embed.add_field(name="​", value="​", inline=True)
        embed.add_field(name="솔로랭크", value=solo_str, inline=True)
        embed.add_field(name="자유랭크", value=flex_str, inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ── /봇상태 ───────────────────────────────────────────────────────────────

    @app_commands.command(name="봇상태", description="봇 레이턴시·DB·Riot API 상태를 확인합니다.")
    async def check_status(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)

        ws_latency = round(self.bot.latency * 1000)

        # DB 응답 확인
        t0 = time.perf_counter()
        try:
            await db.get_user("__health_check__")
            db_ms = round((time.perf_counter() - t0) * 1000)
            db_status = f"✅ {db_ms}ms"
        except Exception:
            db_status = "❌ 오류"

        # Riot API 응답 확인
        t0 = time.perf_counter()
        try:
            api_ok = await riot.check_api_status()
            api_ms = round((time.perf_counter() - t0) * 1000)
            api_status = f"✅ {api_ms}ms" if api_ok else "❌ 오류"
        except Exception:
            api_status = "❌ 오류"

        embed = discord.Embed(title="🤖 봇 상태", color=discord.Color.green())
        embed.add_field(name="WebSocket 레이턴시", value=f"`{ws_latency}ms`", inline=True)
        embed.add_field(name="DB 응답", value=f"`{db_status}`", inline=True)
        embed.add_field(name="Riot API", value=f"`{api_status}`", inline=True)

        open_lobbies = await db.get_open_lobbies()
        embed.add_field(name="진행 중 내전", value=f"{len(open_lobbies)}개", inline=True)

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ProfileCog(bot))
