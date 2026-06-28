import asyncio
import logging
import discord
import uvicorn
from discord.ext import commands, tasks

from config import DISCORD_TOKEN, WEB_PORT
from database import (
    init_db,
    get_open_lobbies,
    get_expired_lobbies,
    get_pending_scheduled_lobbies,
    mark_scheduled_done,
    close_lobby,
    get_server_settings,
    create_lobby,
    update_lobby_message,
    get_user,
    add_lobby_member,
    log_event,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("내전봇")


class LolCustomBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents, help_command=None)

    async def setup_hook(self) -> None:
        from web.app import set_bot
        set_bot(self)

        await init_db()

        await self.load_extension("cogs.lobby")
        await self.load_extension("cogs.profile")
        await self.load_extension("cogs.admin")
        await self.load_extension("cogs.team")

        # 봇 재시작 후 열린 내전 버튼 뷰 복구
        from cogs.lobby import LobbyView

        open_lobbies = await get_open_lobbies()
        for lobby in open_lobbies:
            self.add_view(LobbyView(lobby["id"], self))
        if open_lobbies:
            logger.info(f"복구된 내전 방: {len(open_lobbies)}개")

        synced = await self.tree.sync()
        logger.info(f"슬래시 커맨드 동기화: {len(synced)}개")

        # 백그라운드 태스크 시작
        self.expire_lobbies.start()
        self.fire_scheduled_lobbies.start()

    async def on_ready(self) -> None:
        logger.info(f"봇 로그인 완료: {self.user} (ID: {self.user.id})")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="내전 모집 중 🎮",
            )
        )

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError
    ) -> None:
        logger.error(f"[커맨드 오류] {interaction.command}: {error}")
        guild_id = str(interaction.guild_id) if interaction.guild_id else ""
        await self._send_log(
            guild_id,
            f"⚠️ **커맨드 오류** `/{interaction.command}`\n```{error}```",
        )
        if not interaction.response.is_done():
            await interaction.response.send_message(
                "❌ 명령어 처리 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
                ephemeral=True,
            )

    async def _send_log(self, guild_id: str, content: str) -> None:
        if not guild_id:
            return
        settings = await get_server_settings(guild_id)
        log_ch_id = settings.get("log_channel_id")
        if not log_ch_id:
            return
        try:
            ch = self.get_channel(int(log_ch_id)) or await self.fetch_channel(int(log_ch_id))
            await ch.send(content)
        except Exception:
            pass

    # ── 백그라운드 태스크 ──────────────────────────────────────────────────────

    @tasks.loop(minutes=30)
    async def expire_lobbies(self) -> None:
        """12시간 이상 열린 내전 자동 만료 — 개설자에게만 DM"""
        expired = await get_expired_lobbies(hours=12)
        for lobby in expired:
            await close_lobby(lobby["id"], "cancelled")
            await log_event(
                "lobby_expired",
                guild_id=lobby.get("guild_id"),
                lobby_id=lobby["id"],
                discord_id=lobby.get("creator_discord_id"),
                detail="12시간 자동 만료",
            )
            logger.info(f"[자동 만료] 내전 ID {lobby['id']} (12시간 초과)")

            try:
                ch = self.get_channel(int(lobby["channel_id"])) or await self.fetch_channel(
                    int(lobby["channel_id"])
                )
                if lobby.get("message_id"):
                    msg = await ch.fetch_message(int(lobby["message_id"]))
                    embed = discord.Embed(
                        title="⏰ 내전 자동 만료",
                        description="12시간이 지나 내전이 자동으로 종료되었습니다.",
                        color=discord.Color.orange(),
                    )
                    await msg.edit(embed=embed, view=None)
            except Exception as e:
                logger.warning(f"[자동 만료] 메시지 수정 실패: {e}")

            # 개설자에게만 DM
            try:
                creator = self.get_user(int(lobby["creator_discord_id"])) or \
                          await self.fetch_user(int(lobby["creator_discord_id"]))
                await creator.send(
                    f"⏰ **내전이 자동 종료되었습니다.**\n"
                    f"개설하신 내전(ID: {lobby['id']})이 12시간이 지나 자동으로 종료되었습니다."
                )
            except Exception:
                pass

    @expire_lobbies.before_loop
    async def before_expire(self) -> None:
        await self.wait_until_ready()

    @tasks.loop(minutes=1)
    async def fire_scheduled_lobbies(self) -> None:
        """예약된 내전 자동 개설"""
        from cogs.lobby import LobbyView, build_lobby_embed

        pending = await get_pending_scheduled_lobbies()
        for sched in pending:
            try:
                creator_id = sched["creator_discord_id"]
                guild_id = sched["guild_id"]
                channel_id = sched["channel_id"]

                ch = self.get_channel(int(channel_id)) or await self.fetch_channel(int(channel_id))

                lobby_id = await create_lobby(creator_id, channel_id, guild_id)
                from database import get_lobby as _get_lobby
                lobby = await _get_lobby(lobby_id)

                settings = await get_server_settings(guild_id)
                alert_role_id = settings.get("alert_role_id")
                content = f"<@{creator_id}>님이 내전 맴버를 모집합니다. (예약 개설)"
                if alert_role_id:
                    content = f"<@&{alert_role_id}> {content}"

                view = LobbyView(lobby_id, self)
                self.add_view(view)
                embed = build_lobby_embed(lobby, [])
                msg = await ch.send(content=content, embed=embed, view=view)
                await update_lobby_message(lobby_id, str(msg.id))

                user = await get_user(creator_id)
                if user and user.get("game_name"):
                    await add_lobby_member(
                        lobby_id, creator_id, position=user.get("main_role", "무관")
                    )

                await mark_scheduled_done(sched["id"])
                logger.info(f"[예약 개설] 내전 ID {lobby_id} by <@{creator_id}>")
            except Exception as e:
                logger.error(f"[예약 개설] 오류: {e}")

    @fire_scheduled_lobbies.before_loop
    async def before_fire(self) -> None:
        await self.wait_until_ready()


def main() -> None:
    if not DISCORD_TOKEN:
        logger.error("DISCORD_TOKEN이 설정되지 않았습니다. .env 파일을 확인하세요.")
        raise SystemExit(1)

    from web.app import app as web_app

    async def _run() -> None:
        bot = LolCustomBot()

        web_config = uvicorn.Config(
            web_app,
            host="0.0.0.0",
            port=WEB_PORT,
            log_level="warning",
        )
        web_server = uvicorn.Server(web_config)
        web_server.install_signal_handlers = lambda: None  # asyncio가 시그널 처리

        logger.info(f"웹 관리자 페이지: http://localhost:{WEB_PORT}")
        await asyncio.gather(
            bot.start(DISCORD_TOKEN),
            web_server.serve(),
        )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
