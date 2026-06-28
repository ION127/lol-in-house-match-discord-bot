import aiosqlite
from typing import Optional
from config import DATABASE_PATH


async def init_db() -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                discord_id    TEXT PRIMARY KEY,
                discord_name  TEXT,
                puuid         TEXT UNIQUE,
                game_name     TEXT,
                tag_line      TEXT,
                main_role     TEXT DEFAULT '무관',
                sub_role      TEXT DEFAULT '무관',
                solo_tier     TEXT DEFAULT 'UNRANKED',
                solo_rank     TEXT,
                solo_lp       INTEGER DEFAULT 0,
                flex_tier     TEXT DEFAULT 'UNRANKED',
                flex_rank     TEXT,
                flex_lp       INTEGER DEFAULT 0,
                updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS lobbies (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                guild_id            TEXT,
                creator_discord_id  TEXT NOT NULL,
                channel_id          TEXT NOT NULL,
                message_id          TEXT,
                status              TEXT DEFAULT 'open',
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS lobby_members (
                lobby_id    INTEGER,
                discord_id  TEXT,
                position    TEXT DEFAULT '무관',
                team        TEXT DEFAULT '미정',
                joined_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (lobby_id, discord_id),
                FOREIGN KEY (lobby_id) REFERENCES lobbies(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS waitlist (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                lobby_id    INTEGER NOT NULL,
                discord_id  TEXT NOT NULL,
                position    TEXT DEFAULT '무관',
                team        TEXT DEFAULT '미정',
                joined_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(lobby_id, discord_id),
                FOREIGN KEY (lobby_id) REFERENCES lobbies(id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS server_settings (
                guild_id            TEXT PRIMARY KEY,
                alert_role_id       TEXT,
                log_channel_id      TEXT,
                cooldown_minutes    INTEGER DEFAULT 5,
                max_members         INTEGER DEFAULT 10,
                updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS cooldowns (
                discord_id      TEXT PRIMARY KEY,
                last_cancel_at  TIMESTAMP
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS banned_users (
                guild_id      TEXT,
                discord_id    TEXT,
                banned_until  TIMESTAMP,
                reason        TEXT,
                PRIMARY KEY (guild_id, discord_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS scheduled_lobbies (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                creator_discord_id  TEXT NOT NULL,
                guild_id            TEXT NOT NULL,
                channel_id          TEXT NOT NULL,
                fire_at             TIMESTAMP NOT NULL,
                status              TEXT DEFAULT 'pending',
                created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.execute("""
            CREATE TABLE IF NOT EXISTS bot_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                event_type  TEXT NOT NULL,
                guild_id    TEXT,
                lobby_id    INTEGER,
                discord_id  TEXT,
                detail      TEXT,
                created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # 마이그레이션: 기존 DB에 새 컬럼 추가
        for sql in [
            "ALTER TABLE lobbies ADD COLUMN guild_id TEXT",
            "ALTER TABLE lobby_members ADD COLUMN position TEXT DEFAULT '무관'",
            "ALTER TABLE lobby_members ADD COLUMN team TEXT DEFAULT '미정'",
            "ALTER TABLE waitlist ADD COLUMN team TEXT DEFAULT '미정'",
            "ALTER TABLE users ADD COLUMN discord_name TEXT",
        ]:
            try:
                await db.execute(sql)
            except Exception:
                pass  # 이미 존재하는 컬럼

        await db.commit()


# ── Users ─────────────────────────────────────────────────────────────────────

async def get_user(discord_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM users WHERE discord_id = ?", (discord_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def upsert_user(discord_id: str, **kwargs) -> None:
    cols = ["discord_id"] + list(kwargs.keys())
    vals = [discord_id] + list(kwargs.values())
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{k} = excluded.{k}" for k in kwargs)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            f"""
            INSERT INTO users ({", ".join(cols)}) VALUES ({placeholders})
            ON CONFLICT(discord_id) DO UPDATE SET {updates},
                updated_at = CURRENT_TIMESTAMP
            """,
            vals,
        )
        await db.commit()


# ── Lobbies ───────────────────────────────────────────────────────────────────

async def create_lobby(creator_discord_id: str, channel_id: str, guild_id: str) -> int:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "INSERT INTO lobbies (creator_discord_id, channel_id, guild_id) VALUES (?, ?, ?)",
            (creator_discord_id, channel_id, guild_id),
        )
        await db.commit()
        return cur.lastrowid


async def get_lobby(lobby_id: int) -> Optional[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM lobbies WHERE id = ?", (lobby_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_active_lobby_by_creator(creator_discord_id: str) -> Optional[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM lobbies WHERE creator_discord_id = ? AND status = 'open'",
            (creator_discord_id,),
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_open_lobbies() -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM lobbies WHERE status = 'open'") as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_expired_lobbies(hours: int = 24) -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM lobbies WHERE status IN ('open', 'full')
               AND created_at <= datetime('now', ?)""",
            (f"-{hours} hours",),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def update_lobby_message(lobby_id: int, message_id: str) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE lobbies SET message_id = ? WHERE id = ?", (message_id, lobby_id)
        )
        await db.commit()


async def close_lobby(lobby_id: int, status: str = "cancelled") -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE lobbies SET status = ? WHERE id = ?", (status, lobby_id)
        )
        await db.commit()


# ── Lobby Members ─────────────────────────────────────────────────────────────

async def add_lobby_member(lobby_id: int, discord_id: str, position: str = "무관", team: str = "미정") -> bool:
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute(
                "INSERT INTO lobby_members (lobby_id, discord_id, position, team) VALUES (?, ?, ?, ?)",
                (lobby_id, discord_id, position, team),
            )
            await db.commit()
        return True
    except aiosqlite.IntegrityError:
        return False


async def remove_lobby_member(lobby_id: int, discord_id: str) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "DELETE FROM lobby_members WHERE lobby_id = ? AND discord_id = ?",
            (lobby_id, discord_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def is_member_in_lobby(lobby_id: int, discord_id: str) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM lobby_members WHERE lobby_id = ? AND discord_id = ?",
            (lobby_id, discord_id),
        ) as cur:
            return await cur.fetchone() is not None


async def get_lobby_member_count(lobby_id: int) -> int:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM lobby_members WHERE lobby_id = ?", (lobby_id,)
        ) as cur:
            row = await cur.fetchone()
            return row[0] if row else 0


async def get_lobby_members(lobby_id: int) -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """
            SELECT lm.discord_id, lm.position, lm.team, lm.joined_at,
                   u.game_name, u.tag_line,
                   u.main_role, u.sub_role,
                   u.solo_tier, u.solo_rank, u.solo_lp,
                   u.flex_tier, u.flex_rank, u.flex_lp
            FROM lobby_members lm
            LEFT JOIN users u ON lm.discord_id = u.discord_id
            WHERE lm.lobby_id = ?
            ORDER BY lm.joined_at
            """,
            (lobby_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


# ── Waitlist ──────────────────────────────────────────────────────────────────

async def add_to_waitlist(lobby_id: int, discord_id: str, position: str = "무관", team: str = "미정") -> bool:
    try:
        async with aiosqlite.connect(DATABASE_PATH) as db:
            await db.execute(
                "INSERT INTO waitlist (lobby_id, discord_id, position, team) VALUES (?, ?, ?, ?)",
                (lobby_id, discord_id, position, team),
            )
            await db.commit()
        return True
    except aiosqlite.IntegrityError:
        return False


async def remove_from_waitlist(lobby_id: int, discord_id: str) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "DELETE FROM waitlist WHERE lobby_id = ? AND discord_id = ?",
            (lobby_id, discord_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def get_waitlist(lobby_id: int) -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM waitlist WHERE lobby_id = ? ORDER BY joined_at",
            (lobby_id,),
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_waitlist_position(lobby_id: int, discord_id: str) -> int:
    """대기열 순번 반환 (1-based). 0이면 대기열에 없음."""
    waitlist = await get_waitlist(lobby_id)
    for i, entry in enumerate(waitlist, 1):
        if entry["discord_id"] == discord_id:
            return i
    return 0


async def pop_first_waitlist(lobby_id: int) -> Optional[dict]:
    """대기열 첫 번째 항목을 꺼내고 삭제"""
    waitlist = await get_waitlist(lobby_id)
    if not waitlist:
        return None
    first = waitlist[0]
    await remove_from_waitlist(lobby_id, first["discord_id"])
    return first


# ── Server Settings ───────────────────────────────────────────────────────────

async def get_server_settings(guild_id: str) -> dict:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM server_settings WHERE guild_id = ?", (guild_id,)
        ) as cur:
            row = await cur.fetchone()
            if row:
                return dict(row)
    # 기본값 반환
    return {
        "guild_id": guild_id,
        "alert_role_id": None,
        "log_channel_id": None,
        "cooldown_minutes": 5,
        "max_members": 10,
    }


async def upsert_server_settings(guild_id: str, **kwargs) -> None:
    cols = ["guild_id"] + list(kwargs.keys())
    vals = [guild_id] + list(kwargs.values())
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{k} = excluded.{k}" for k in kwargs)
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            f"""
            INSERT INTO server_settings ({", ".join(cols)}) VALUES ({placeholders})
            ON CONFLICT(guild_id) DO UPDATE SET {updates},
                updated_at = CURRENT_TIMESTAMP
            """,
            vals,
        )
        await db.commit()


# ── Cooldowns ─────────────────────────────────────────────────────────────────

async def set_cooldown(discord_id: str) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO cooldowns (discord_id, last_cancel_at)
               VALUES (?, CURRENT_TIMESTAMP)
               ON CONFLICT(discord_id) DO UPDATE SET last_cancel_at = CURRENT_TIMESTAMP""",
            (discord_id,),
        )
        await db.commit()


async def get_cooldown_remaining(discord_id: str, cooldown_minutes: int) -> int:
    """남은 쿨타임(초) 반환. 0이면 쿨타임 없음."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute(
            """SELECT CAST((julianday(CURRENT_TIMESTAMP) - julianday(last_cancel_at))
                    * 86400 AS INTEGER) AS elapsed_seconds
               FROM cooldowns WHERE discord_id = ?""",
            (discord_id,),
        ) as cur:
            row = await cur.fetchone()
            if not row:
                return 0
            elapsed = row[0]
            remaining = cooldown_minutes * 60 - elapsed
            return max(0, remaining)


# ── Banned Users ──────────────────────────────────────────────────────────────

async def ban_user(guild_id: str, discord_id: str, banned_until: Optional[str], reason: str = "") -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO banned_users (guild_id, discord_id, banned_until, reason)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(guild_id, discord_id) DO UPDATE SET
                   banned_until = excluded.banned_until,
                   reason = excluded.reason""",
            (guild_id, discord_id, banned_until, reason),
        )
        await db.commit()


async def unban_user(guild_id: str, discord_id: str) -> bool:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            "DELETE FROM banned_users WHERE guild_id = ? AND discord_id = ?",
            (guild_id, discord_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def is_banned(guild_id: str, discord_id: str) -> tuple[bool, Optional[str]]:
    """(차단여부, 사유) 반환. 만료된 차단은 자동 해제."""
    async with aiosqlite.connect(DATABASE_PATH) as db:
        async with db.execute(
            """SELECT reason, banned_until FROM banned_users
               WHERE guild_id = ? AND discord_id = ?
               AND (banned_until IS NULL OR banned_until > CURRENT_TIMESTAMP)""",
            (guild_id, discord_id),
        ) as cur:
            row = await cur.fetchone()
            if row:
                return True, row[0]

    # 만료된 차단 정리
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """DELETE FROM banned_users
               WHERE guild_id = ? AND discord_id = ? AND banned_until <= CURRENT_TIMESTAMP""",
            (guild_id, discord_id),
        )
        await db.commit()
    return False, None


# ── Scheduled Lobbies ─────────────────────────────────────────────────────────

async def add_scheduled_lobby(
    creator_discord_id: str, guild_id: str, channel_id: str, fire_at: str
) -> int:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        cur = await db.execute(
            """INSERT INTO scheduled_lobbies
               (creator_discord_id, guild_id, channel_id, fire_at)
               VALUES (?, ?, ?, ?)""",
            (creator_discord_id, guild_id, channel_id, fire_at),
        )
        await db.commit()
        return cur.lastrowid


async def get_pending_scheduled_lobbies() -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            """SELECT * FROM scheduled_lobbies
               WHERE status = 'pending' AND fire_at <= datetime('now')"""
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def mark_scheduled_done(scheduled_id: int) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            "UPDATE scheduled_lobbies SET status = 'done' WHERE id = ?", (scheduled_id,)
        )
        await db.commit()


# ── Bot Logs ──────────────────────────────────────────────────────────────────

async def log_event(
    event_type: str,
    guild_id: Optional[str] = None,
    lobby_id: Optional[int] = None,
    discord_id: Optional[str] = None,
    detail: Optional[str] = None,
) -> None:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO bot_logs (event_type, guild_id, lobby_id, discord_id, detail)
               VALUES (?, ?, ?, ?, ?)""",
            (event_type, guild_id, lobby_id, discord_id, detail),
        )
        await db.commit()


async def get_logs(
    limit: int = 300,
    event_type: Optional[str] = None,
    guild_id: Optional[str] = None,
) -> list[dict]:
    async with aiosqlite.connect(DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        clauses: list[str] = []
        params: list = []
        if event_type:
            clauses.append("event_type = ?")
            params.append(event_type)
        if guild_id:
            clauses.append("guild_id = ?")
            params.append(guild_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        async with db.execute(
            f"SELECT * FROM bot_logs {where} ORDER BY created_at DESC LIMIT ?",
            params,
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]
