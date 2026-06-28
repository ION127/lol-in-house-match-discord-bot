import os
import re
import sys
import discord
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# 부모 디렉토리를 sys.path에 추가 (database, config import용)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database as db
from config import WEB_SECRET, DATABASE_PATH

_bot = None
_static_dir = os.path.join(os.path.dirname(__file__), "static")


def set_bot(bot) -> None:
    global _bot
    _bot = bot


def get_bot():
    return _bot


app = FastAPI(title="내전봇 관리자 API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── 인증 ──────────────────────────────────────────────────────────────────────

async def check_auth(authorization: str = Header(...)) -> None:
    if not authorization.startswith("Bearer ") or authorization[7:] != WEB_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")


class AuthBody(BaseModel):
    password: str


@app.post("/api/auth")
async def auth_login(body: AuthBody):
    if body.password == WEB_SECRET:
        return {"token": WEB_SECRET}
    raise HTTPException(status_code=401, detail="Invalid password")


# ── 서버(길드) 목록 ───────────────────────────────────────────────────────────

@app.get("/api/guilds", dependencies=[Depends(check_auth)])
async def get_guilds():
    bot = get_bot()
    if not bot:
        return {"guilds": []}
    return {
        "guilds": [
            {"id": str(g.id), "name": g.name, "member_count": g.member_count}
            for g in bot.guilds
        ]
    }


# ── 대시보드 ──────────────────────────────────────────────────────────────────

@app.get("/api/dashboard", dependencies=[Depends(check_auth)])
async def get_dashboard():
    import aiosqlite

    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row

        async with conn.execute("SELECT COUNT(*) FROM lobbies WHERE status='open'") as c:
            active = (await c.fetchone())[0]
        async with conn.execute("SELECT COUNT(*) FROM lobbies WHERE status='full'") as c:
            completed = (await c.fetchone())[0]
        async with conn.execute("SELECT COUNT(*) FROM lobbies WHERE status='cancelled'") as c:
            cancelled_count = (await c.fetchone())[0]
        async with conn.execute("SELECT COUNT(*) FROM users") as c:
            total_users = (await c.fetchone())[0]
        async with conn.execute(
            "SELECT COUNT(*) FROM banned_users WHERE banned_until IS NULL OR banned_until > CURRENT_TIMESTAMP"
        ) as c:
            banned = (await c.fetchone())[0]

        async with conn.execute("""
            SELECT l.id, l.creator_discord_id, l.channel_id, l.guild_id, l.created_at,
                   COUNT(lm.discord_id) as member_count
            FROM lobbies l
            LEFT JOIN lobby_members lm ON l.id = lm.lobby_id
            WHERE l.status = 'open'
            GROUP BY l.id
            ORDER BY l.created_at DESC
        """) as c:
            open_lobbies = [dict(r) for r in await c.fetchall()]

    return {
        "active_lobbies": active,
        "completed_lobbies": completed,
        "cancelled_lobbies": cancelled_count,
        "total_users": total_users,
        "banned_users": banned,
        "open_lobbies": open_lobbies,
    }


# ── 내전 관리 ─────────────────────────────────────────────────────────────────

@app.get("/api/lobbies", dependencies=[Depends(check_auth)])
async def get_lobbies(status: str = "all", guild_id: str = ""):
    import aiosqlite

    clauses, params = [], []
    if status != "all":
        clauses.append("l.status = ?")
        params.append(status)
    if guild_id:
        clauses.append("l.guild_id = ?")
        params.append(guild_id)

    where = "WHERE " + " AND ".join(clauses) if clauses else ""

    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        async with conn.execute(f"""
            SELECT l.id, l.creator_discord_id, l.channel_id, l.guild_id,
                   l.message_id, l.status, l.created_at,
                   COUNT(lm.discord_id) as member_count
            FROM lobbies l
            LEFT JOIN lobby_members lm ON l.id = lm.lobby_id
            {where}
            GROUP BY l.id
            ORDER BY l.created_at DESC
            LIMIT 200
        """, params) as c:
            lobbies = [dict(r) for r in await c.fetchall()]

    return {"lobbies": lobbies}


@app.get("/api/lobbies/{lobby_id}", dependencies=[Depends(check_auth)])
async def get_lobby_detail(lobby_id: int):
    lobby = await db.get_lobby(lobby_id)
    if not lobby:
        raise HTTPException(status_code=404, detail="Lobby not found")
    members = await db.get_lobby_members(lobby_id)
    waitlist = await db.get_waitlist(lobby_id)
    return {"lobby": lobby, "members": members, "waitlist": waitlist}


@app.delete("/api/lobbies/{lobby_id}/members/{discord_id}", dependencies=[Depends(check_auth)])
async def kick_lobby_member(lobby_id: int, discord_id: str):
    """특정 멤버를 내전에서 강제 퇴장. 대기열 자동 승격 + 디스코드 임베드 갱신."""
    import aiosqlite

    lobby = await db.get_lobby(lobby_id)
    if not lobby:
        raise HTTPException(status_code=404, detail="Lobby not found")
    if lobby["status"] not in ("open", "full"):
        raise HTTPException(status_code=400, detail="Lobby is not active")

    removed = await db.remove_lobby_member(lobby_id, discord_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Member not in lobby")

    await db.log_event(
        "member_kicked",
        guild_id=lobby.get("guild_id"),
        lobby_id=lobby_id,
        discord_id=discord_id,
        detail="웹 관리자",
    )

    # full 상태였으면 자리가 생겼으므로 open 으로 되돌림
    if lobby["status"] == "full":
        async with aiosqlite.connect(DATABASE_PATH) as conn:
            await conn.execute("UPDATE lobbies SET status='open' WHERE id=?", (lobby_id,))
            await conn.commit()

    bot = get_bot()
    if bot:
        # 내보낸 유저 DM
        try:
            user = bot.get_user(int(discord_id)) or await bot.fetch_user(int(discord_id))
            await user.send(
                f"❌ **관리자에 의해 내전에서 제외되었습니다.**\n"
                f"내전 ID: `#{lobby_id}` | 개설자: <@{lobby['creator_discord_id']}> | 채널: <#{lobby['channel_id']}>"
            )
        except Exception:
            pass

        # 대기열 첫 번째 자동 승격
        first = await db.pop_first_waitlist(lobby_id)
        if first:
            added = await db.add_lobby_member(
                lobby_id, first["discord_id"],
                position=first["position"], team=first.get("team", "미정"),
            )
            if added:
                _POS = {"탑": "🔝", "정글": "🌲", "미드": "⚔️", "원딜": "🏹", "서폿": "🛡️", "무관": "🎲"}
                try:
                    promoted = bot.get_user(int(first["discord_id"])) or await bot.fetch_user(int(first["discord_id"]))
                    t_emoji = "🔵" if first.get("team") == "1팀" else "🔴" if first.get("team") == "2팀" else "⬜"
                    await promoted.send(
                        f"🎮 **내전 자리가 생겼습니다!**\n"
                        f"대기 중이던 내전에 자동으로 참가되었습니다.\n"
                        f"팀: {t_emoji} {first.get('team', '미정')} | 포지션: {_POS.get(first['position'], '🎲')} {first['position']}"
                    )
                except Exception:
                    pass

        # 디스코드 내전 임베드 갱신
        try:
            from cogs.lobby import refresh_lobby_embed
            await refresh_lobby_embed(bot, lobby_id)
        except Exception:
            pass

    return {"success": True}


@app.post("/api/lobbies/{lobby_id}/cancel", dependencies=[Depends(check_auth)])
async def cancel_lobby(lobby_id: int):
    lobby = await db.get_lobby(lobby_id)
    if not lobby:
        raise HTTPException(status_code=404, detail="Lobby not found")
    if lobby["status"] not in ("open", "full"):
        raise HTTPException(status_code=400, detail="Lobby is not active")

    members = await db.get_lobby_members(lobby_id)
    await db.close_lobby(lobby_id, "cancelled")
    await db.log_event(
        "lobby_cancelled",
        guild_id=lobby.get("guild_id"),
        lobby_id=lobby_id,
        discord_id=lobby.get("creator_discord_id"),
        detail="웹 관리자 강제 취소",
    )

    bot = get_bot()
    if bot:
        # 디스코드 메시지 수정
        try:
            ch = (
                bot.get_channel(int(lobby["channel_id"]))
                or await bot.fetch_channel(int(lobby["channel_id"]))
            )
            if lobby.get("message_id"):
                msg = await ch.fetch_message(int(lobby["message_id"]))
                embed = discord.Embed(
                    title="❌ 내전 강제 취소됨",
                    description="관리자에 의해 내전이 취소되었습니다.",
                    color=discord.Color.red(),
                )
                await msg.edit(embed=embed, view=None)
        except Exception:
            pass

        # 참가자 전원 DM
        for m in members:
            try:
                user = bot.get_user(int(m["discord_id"])) or await bot.fetch_user(
                    int(m["discord_id"])
                )
                await user.send(
                    f"❌ **참가 중이던 내전이 관리자에 의해 취소되었습니다.**\n"
                    f"내전 ID: `#{lobby_id}` | 개설자: <@{lobby['creator_discord_id']}> | 채널: <#{lobby['channel_id']}>"
                )
            except Exception:
                pass

    return {"success": True}


# ── 유저 관리 ─────────────────────────────────────────────────────────────────

@app.get("/api/users", dependencies=[Depends(check_auth)])
async def get_users(search: str = ""):
    import aiosqlite

    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        if search:
            async with conn.execute(
                """SELECT * FROM users
                   WHERE discord_id LIKE ? OR game_name LIKE ? OR tag_line LIKE ?
                   ORDER BY updated_at DESC LIMIT 200""",
                (f"%{search}%", f"%{search}%", f"%{search}%"),
            ) as c:
                users = [dict(r) for r in await c.fetchall()]
        else:
            async with conn.execute(
                "SELECT * FROM users ORDER BY updated_at DESC LIMIT 200"
            ) as c:
                users = [dict(r) for r in await c.fetchall()]

    return {"users": users}


@app.delete("/api/users/{discord_id}", dependencies=[Depends(check_auth)])
async def delete_user(discord_id: str):
    import aiosqlite

    async with aiosqlite.connect(DATABASE_PATH) as conn:
        await conn.execute("DELETE FROM users WHERE discord_id = ?", (discord_id,))
        await conn.commit()
    return {"success": True}


class BanBody(BaseModel):
    guild_id: str
    duration: str = ""
    reason: str = ""


@app.post("/api/users/{discord_id}/ban", dependencies=[Depends(check_auth)])
async def ban_user(discord_id: str, body: BanBody):
    banned_until = _parse_ban_duration(body.duration)
    banned_until_str = (
        banned_until.strftime("%Y-%m-%d %H:%M:%S") if banned_until else None
    )
    await db.ban_user(body.guild_id, discord_id, banned_until_str, body.reason)
    await db.log_event(
        "user_banned",
        guild_id=body.guild_id,
        discord_id=discord_id,
        detail=f"기간: {body.duration or '영구'}, 사유: {body.reason or '없음'}",
    )

    bot = get_bot()
    if bot:
        try:
            user = bot.get_user(int(discord_id)) or await bot.fetch_user(int(discord_id))
            duration_text = f"{body.duration} 동안" if banned_until else "영구"
            await user.send(
                f"🔨 내전 참가가 **{duration_text}** 금지되었습니다.\n"
                f"사유: {body.reason or '없음'}"
            )
        except Exception:
            pass

    return {"success": True}


class UnbanBody(BaseModel):
    guild_id: str


@app.post("/api/users/{discord_id}/unban", dependencies=[Depends(check_auth)])
async def unban_user(discord_id: str, body: UnbanBody):
    removed = await db.unban_user(body.guild_id, discord_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Ban record not found")
    await db.log_event("user_unbanned", guild_id=body.guild_id, discord_id=discord_id)
    return {"success": True}


# ── 설정 ──────────────────────────────────────────────────────────────────────

@app.get("/api/settings/{guild_id}", dependencies=[Depends(check_auth)])
async def get_settings(guild_id: str):
    return await db.get_server_settings(guild_id)


class SettingsBody(BaseModel):
    alert_role_id: Optional[str] = None
    log_channel_id: Optional[str] = None
    cooldown_minutes: int = 5
    max_members: int = 10


@app.post("/api/settings/{guild_id}", dependencies=[Depends(check_auth)])
async def save_settings(guild_id: str, body: SettingsBody):
    await db.upsert_server_settings(
        guild_id,
        alert_role_id=body.alert_role_id or None,
        log_channel_id=body.log_channel_id or None,
        cooldown_minutes=body.cooldown_minutes,
        max_members=body.max_members,
    )
    await db.log_event(
        "settings_changed",
        guild_id=guild_id,
        detail=f"최대인원: {body.max_members}, 쿨타임: {body.cooldown_minutes}분",
    )
    return {"success": True}


# ── 로그 ─────────────────────────────────────────────────────────────────────

@app.get("/api/logs", dependencies=[Depends(check_auth)])
async def get_logs(limit: int = 300, event_type: str = "", guild_id: str = ""):
    return {"logs": await db.get_logs(
        limit=min(limit, 1000),
        event_type=event_type or None,
        guild_id=guild_id or None,
    )}


# ── 차단 유저 목록 ────────────────────────────────────────────────────────────

@app.get("/api/bans", dependencies=[Depends(check_auth)])
async def get_bans(guild_id: str = ""):
    import aiosqlite

    async with aiosqlite.connect(DATABASE_PATH) as conn:
        conn.row_factory = aiosqlite.Row
        clause = "WHERE (banned_until IS NULL OR banned_until > CURRENT_TIMESTAMP)"
        params = []
        if guild_id:
            clause += " AND guild_id = ?"
            params.append(guild_id)
        async with conn.execute(
            f"SELECT * FROM banned_users {clause} ORDER BY guild_id, discord_id",
            params,
        ) as c:
            bans = [dict(r) for r in await c.fetchall()]

    return {"bans": bans}


# ── 정적 파일 서빙 ────────────────────────────────────────────────────────────

@app.get("/")
async def root():
    return FileResponse(os.path.join(_static_dir, "index.html"))


@app.get("/{path:path}")
async def catch_all(path: str):
    file_path = os.path.join(_static_dir, path)
    if os.path.isfile(file_path):
        return FileResponse(file_path)
    return FileResponse(os.path.join(_static_dir, "index.html"))


# ── 헬퍼 ──────────────────────────────────────────────────────────────────────

def _parse_ban_duration(s: str) -> Optional[datetime]:
    if not s:
        return None
    total = 0
    for m in re.finditer(r"(\d+)\s*(일|d|시간|h|분|m)", s):
        val, unit = int(m.group(1)), m.group(2)
        if unit in ("일", "d"):
            total += val * 86400
        elif unit in ("시간", "h"):
            total += val * 3600
        elif unit in ("분", "m"):
            total += val * 60
    return (datetime.now(timezone.utc) + timedelta(seconds=total)) if total > 0 else None
