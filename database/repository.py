import json
from typing import Optional, List, Dict, Any
from database.db import get_db_connection


class Repository:
    # ----------------- EPISODES -----------------
    @staticmethod
    async def add_episode(
        season: int,
        episode_number: int,
        title: str,
        content: str,
        cliffhanger: Optional[str] = None,
        poll_id: Optional[str] = None,
        telegram_message_id: Optional[int] = None
    ) -> int:
        async with get_db_connection() as conn:
            cursor = await conn.execute("""
                INSERT INTO episodes (season, episode_number, title, content, cliffhanger, poll_id, telegram_message_id)
                VALUES (?, ?, ?, ?, ?, ?, ?);
            """, (season, episode_number, title, content, cliffhanger, poll_id, telegram_message_id))
            await conn.commit()
            return cursor.lastrowid

    @staticmethod
    async def update_episode_meta(episode_id: int, poll_id: Optional[str] = None, telegram_message_id: Optional[int] = None) -> None:
        async with get_db_connection() as conn:
            await conn.execute("""
                UPDATE episodes
                SET poll_id = COALESCE(?, poll_id),
                    telegram_message_id = COALESCE(?, telegram_message_id)
                WHERE id = ?;
            """, (poll_id, telegram_message_id, episode_id))
            await conn.commit()

    @staticmethod
    async def get_latest_episode(season: int = 1) -> Optional[Dict[str, Any]]:
        async with get_db_connection() as conn:
            cursor = await conn.execute("""
                SELECT * FROM episodes
                WHERE season = ?
                ORDER BY episode_number DESC
                LIMIT 1;
            """, (season,))
            row = await cursor.fetchone()
            return dict(row) if row else None

    @staticmethod
    async def get_recent_episodes(limit: int = 3, season: int = 1) -> List[Dict[str, Any]]:
        async with get_db_connection() as conn:
            cursor = await conn.execute("""
                SELECT * FROM episodes
                WHERE season = ?
                ORDER BY episode_number DESC
                LIMIT ?;
            """, (season, limit))
            rows = await cursor.fetchall()
            return [dict(row) for row in reversed(rows)]

    @staticmethod
    async def get_season_episodes(season: int = 1) -> List[Dict[str, Any]]:
        async with get_db_connection() as conn:
            cursor = await conn.execute("""
                SELECT id, episode_number, title, cliffhanger, published_at FROM episodes
                WHERE season = ?
                ORDER BY episode_number ASC;
            """, (season,))
            rows = await cursor.fetchall()
            return [dict(row) for row in rows]

    @staticmethod
    async def update_episode_content(episode_id: int, content: str, cliffhanger: Optional[str] = None) -> None:
        async with get_db_connection() as conn:
            await conn.execute("""
                UPDATE episodes
                SET content = ?,
                    cliffhanger = COALESCE(?, cliffhanger)
                WHERE id = ?;
            """, (content, cliffhanger, episode_id))
            await conn.commit()

    # ----------------- POLLS -----------------
    @staticmethod
    async def add_poll(
        poll_id: str,
        channel_id: str,
        message_id: int,
        question: str,
        options: List[str],
        episode_id: Optional[int] = None
    ) -> int:
        options_json = json.dumps(options, ensure_ascii=False)
        async with get_db_connection() as conn:
            cursor = await conn.execute("""
                INSERT INTO polls (poll_id, episode_id, channel_id, message_id, question, options_json)
                VALUES (?, ?, ?, ?, ?, ?);
            """, (poll_id, episode_id, str(channel_id), message_id, question, options_json))
            await conn.commit()
            return cursor.lastrowid

    @staticmethod
    async def get_active_poll() -> Optional[Dict[str, Any]]:
        async with get_db_connection() as conn:
            cursor = await conn.execute("""
                SELECT * FROM polls
                WHERE is_closed = 0
                ORDER BY id DESC
                LIMIT 1;
            """)
            row = await cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            res["options"] = json.loads(res["options_json"])
            return res

    @staticmethod
    async def close_poll(poll_id: str, winner_option: str, total_voters: int = 0) -> None:
        async with get_db_connection() as conn:
            await conn.execute("""
                UPDATE polls
                SET is_closed = 1,
                    winner_option = ?,
                    total_voters = ?
                WHERE poll_id = ?;
            """, (winner_option, total_voters, poll_id))
            await conn.commit()

    @staticmethod
    async def get_poll_by_poll_id(poll_id: str) -> Optional[Dict[str, Any]]:
        async with get_db_connection() as conn:
            cursor = await conn.execute("SELECT * FROM polls WHERE poll_id = ?;", (poll_id,))
            row = await cursor.fetchone()
            if not row:
                return None
            res = dict(row)
            res["options"] = json.loads(res["options_json"])
            return res

    # ----------------- STORY STATE -----------------
    @staticmethod
    async def get_story_state(season: int = 1) -> Dict[str, Any]:
        async with get_db_connection() as conn:
            cursor = await conn.execute("SELECT * FROM story_state WHERE season = ? ORDER BY id DESC LIMIT 1;", (season,))
            row = await cursor.fetchone()
            if not row:
                return {
                    "season": season,
                    "current_episode": 0,
                    "rolling_synopsis": "",
                    "active_mysteries": [],
                    "character_statuses": {}
                }
            res = dict(row)
            try:
                res["active_mysteries"] = json.loads(res["active_mysteries_json"])
            except Exception:
                res["active_mysteries"] = []
            try:
                res["character_statuses"] = json.loads(res["character_statuses_json"])
            except Exception:
                res["character_statuses"] = {}
            return res

    @staticmethod
    async def update_story_state(
        season: int,
        current_episode: int,
        rolling_synopsis: str,
        active_mysteries: List[str],
        character_statuses: Dict[str, str]
    ) -> None:
        mysteries_json = json.dumps(active_mysteries, ensure_ascii=False)
        characters_json = json.dumps(character_statuses, ensure_ascii=False)
        async with get_db_connection() as conn:
            await conn.execute("""
                UPDATE story_state
                SET current_episode = ?,
                    rolling_synopsis = ?,
                    active_mysteries_json = ?,
                    character_statuses_json = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE season = ?;
            """, (current_episode, rolling_synopsis, mysteries_json, characters_json, season))
            await conn.commit()

    # ----------------- VIEWER SUGGESTIONS -----------------
    @staticmethod
    async def add_suggestion(
        raw_comment: str,
        extracted_idea: str,
        comment_id: Optional[int] = None,
        chat_id: Optional[str] = None,
        author_username: Optional[str] = None,
        author_name: Optional[str] = None,
        status: str = "pending"
    ) -> int:
        async with get_db_connection() as conn:
            cursor = await conn.execute("""
                INSERT INTO viewer_suggestions (comment_id, chat_id, author_username, author_name, raw_comment, extracted_idea, status)
                VALUES (?, ?, ?, ?, ?, ?, ?);
            """, (comment_id, str(chat_id) if chat_id else None, author_username, author_name, raw_comment, extracted_idea, status))
            await conn.commit()
            return cursor.lastrowid

    @staticmethod
    async def get_pending_suggestions(limit: int = 5) -> List[Dict[str, Any]]:
        async with get_db_connection() as conn:
            cursor = await conn.execute("""
                SELECT * FROM viewer_suggestions
                WHERE status IN ('pending', 'approved')
                ORDER BY CASE WHEN status = 'approved' THEN 0 ELSE 1 END, id ASC
                LIMIT ?;
            """, (limit,))
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    @staticmethod
    async def mark_suggestions_used(suggestion_ids: List[int], episode_id: int) -> None:
        if not suggestion_ids:
            return
        placeholders = ",".join("?" for _ in suggestion_ids)
        async with get_db_connection() as conn:
            await conn.execute(f"""
                UPDATE viewer_suggestions
                SET status = 'used', used_in_episode_id = ?
                WHERE id IN ({placeholders});
            """, [episode_id] + suggestion_ids)
            await conn.commit()

    @staticmethod
    async def update_suggestion_status(suggestion_id: int, status: str) -> None:
        async with get_db_connection() as conn:
            await conn.execute("UPDATE viewer_suggestions SET status = ? WHERE id = ?;", (status, suggestion_id))
            await conn.commit()

    # ----------------- REPLIED COMMENTS -----------------
    @staticmethod
    async def is_comment_replied(chat_id: str, message_id: int) -> bool:
        async with get_db_connection() as conn:
            cursor = await conn.execute("""
                SELECT id FROM replied_comments
                WHERE chat_id = ? AND message_id = ?;
            """, (str(chat_id), message_id))
            row = await cursor.fetchone()
            return row is not None

    @staticmethod
    async def record_replied_comment(
        chat_id: str,
        message_id: int,
        author_name: Optional[str],
        comment_text: Optional[str],
        reply_text: str
    ) -> None:
        async with get_db_connection() as conn:
            await conn.execute("""
                INSERT OR IGNORE INTO replied_comments (chat_id, message_id, author_name, comment_text, reply_text)
                VALUES (?, ?, ?, ?, ?);
            """, (str(chat_id), message_id, author_name, comment_text, reply_text))
            await conn.commit()
