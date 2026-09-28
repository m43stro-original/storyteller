import aiosqlite
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from config import settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def get_db_connection():
    """Asynchronous context manager for SQLite database connection."""
    db_path = settings.database_path
    conn = await aiosqlite.connect(str(db_path))
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys = ON;")
    await conn.execute("PRAGMA journal_mode = WAL;")
    try:
        yield conn
    finally:
        await conn.close()


async def init_db() -> None:
    """Initialize database tables and default initial story state if empty."""
    db_path = settings.database_path
    logger.info(f"Initializing database at {db_path}...")

    async with get_db_connection() as conn:
        # Episodes table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                season INTEGER NOT NULL DEFAULT 1,
                episode_number INTEGER NOT NULL,
                title TEXT NOT NULL,
                content TEXT NOT NULL,
                cliffhanger TEXT,
                poll_id TEXT,
                telegram_message_id INTEGER,
                published_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Polls table
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS polls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poll_id TEXT UNIQUE NOT NULL,
                episode_id INTEGER,
                channel_id TEXT NOT NULL,
                message_id INTEGER,
                question TEXT NOT NULL,
                options_json TEXT NOT NULL,
                winner_option TEXT,
                total_voters INTEGER DEFAULT 0,
                is_closed BOOLEAN DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE SET NULL
            );
        """)

        # Story State Ledger (only 1 active row per season)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS story_state (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                season INTEGER NOT NULL DEFAULT 1,
                current_episode INTEGER NOT NULL DEFAULT 0,
                rolling_synopsis TEXT NOT NULL DEFAULT '',
                active_mysteries_json TEXT NOT NULL DEFAULT '[]',
                character_statuses_json TEXT NOT NULL DEFAULT '{}',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
        """)

        # Viewer suggestions extracted from comments
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS viewer_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                comment_id INTEGER,
                chat_id TEXT,
                author_username TEXT,
                author_name TEXT,
                raw_comment TEXT NOT NULL,
                extracted_idea TEXT NOT NULL,
                status TEXT DEFAULT 'pending',
                used_in_episode_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (used_in_episode_id) REFERENCES episodes(id) ON DELETE SET NULL
            );
        """)

        # Replied comments log (prevents replying twice)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS replied_comments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id TEXT NOT NULL,
                message_id INTEGER NOT NULL,
                author_name TEXT,
                comment_text TEXT,
                reply_text TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(chat_id, message_id)
            );
        """)

        # Ensure a default story_state exists for season 1
        cursor = await conn.execute("SELECT id FROM story_state WHERE season = 1;")
        row = await cursor.fetchone()
        if not row:
            import json
            initial_synopsis = "Кудрово. Начало истории. Знакомство с героями и завязка сюжета."
            initial_mysteries = "[]"
            initial_characters = "{}"
            try:
                lore_file = Path(settings.LORE_BIBLE_PATH)
                if lore_file.exists():
                    with open(lore_file, "r", encoding="utf-8") as f:
                        lore_data = json.load(f)
                    chars = {c.get("name", "Герой"): "В Кудрово" for c in lore_data.get("core_characters", [])}
                    if chars:
                        initial_characters = json.dumps(chars, ensure_ascii=False)
                    if lore_data.get("logline"):
                        initial_synopsis = lore_data["logline"]
            except Exception as e:
                logger.warning(f"Could not load lore_bible for initial story_state: {e}")

            await conn.execute("""
                INSERT INTO story_state (season, current_episode, rolling_synopsis, active_mysteries_json, character_statuses_json)
                VALUES (1, 0, ?, ?, ?);
            """, (initial_synopsis, initial_mysteries, initial_characters))

        await conn.commit()
    logger.info("Database initialized successfully.")
