import pytest
import json
from unittest.mock import AsyncMock, patch, MagicMock
from services.narrator import NarratorService
from services.comment_agent import CommentAgent
from scheduler.episode_job import send_chunked_message


def test_narrator_json_cleaner():
    service = NarratorService()

    # Case 1: Pure JSON
    pure = '{"title": "Тест", "screenplay": "Сцена 1", "cliffhanger": "Конец", "poll_question": "Куда?", "poll_options": ["А", "Б"]}'
    assert service._clean_json_output(pure)["title"] == "Тест"

    # Case 2: Markdown code fence ```json ... ```
    fenced = f"```json\n{pure}\n```"
    assert service._clean_json_output(fenced)["title"] == "Тест"

    # Case 3: Extra text before and after
    messy = f"Вот ваш ответ:\n```json\n{pure}\n```\nНадеюсь вам понравилось!"
    assert service._clean_json_output(messy)["title"] == "Тест"


def test_comment_agent_json_cleaner():
    agent = CommentAgent()

    data = {
        "should_reply": True,
        "reply_text": "Диспетчер слушает: не шумите в коридоре.",
        "is_story_suggestion": True,
        "extracted_suggestion": "Добавить кота в лифт"
    }
    raw = f"```json\n{json.dumps(data)}\n```"
    res = agent._clean_json_output(raw)
    assert res["should_reply"] is True
    assert "кота" in res["extracted_suggestion"]


@pytest.mark.asyncio
async def test_send_chunked_message():
    bot = AsyncMock()
    mock_msg = MagicMock()
    mock_msg.message_id = 999
    bot.send_message.return_value = mock_msg

    # Short message
    short_text = "Короткий текст"
    last_id = await send_chunked_message(bot, chat_id=123, text=short_text)
    assert last_id == 999
    assert bot.send_message.call_count == 1

    # Long message (> 4000 characters)
    bot.send_message.reset_mock()
    long_paragraph = "Кудрово это город контрастов. " * 200  # ~6000 chars
    last_id = await send_chunked_message(bot, chat_id=123, text=long_paragraph)
    assert bot.send_message.call_count >= 2


@pytest.mark.asyncio
async def test_narrator_autonomous_suggestion_triage(monkeypatch):
    from database.db import init_db
    from database.repository import Repository
    monkeypatch.setattr("config.settings.DB_PATH", "data/test_triage.db")
    import os
    if os.path.exists("data/test_triage.db"):
        os.remove("data/test_triage.db")
    await init_db()

    # Create 3 suggestions:
    # 1: wild sharp twist -> will be used
    # 2: utter nonsense -> will be rejected
    # 3: normal twist -> unmentioned, stays pending
    id1 = await Repository.add_suggestion("А пусть Дима найдет в парке чемодан с советскими рублями", "Чемодан с советскими рублями")
    id2 = await Repository.add_suggestion("ываываыва 123 спам блабла", "Спам бред")
    id3 = await Repository.add_suggestion("Даша уронит ключи от спортзала в люк", "Ключи в люк")

    narrator = NarratorService()
    fake_json = {
        "title": "Случай у Оккервиля",
        "screenplay": "ДИМА: Смотри, Даша, чемодан!\nДАША: Откуда он здесь?",
        "cliffhanger": "Чемодан приоткрылся...",
        "poll_question": "Открыть чемодан?",
        "poll_options": ["Да", "Нет"],
        "used_suggestion_ids": [id1],
        "rejected_suggestion_ids": [id2],
        "updated_synopsis": "Дима и Даша нашли чемодан.",
        "new_mysteries": ["Чей чемодан?"],
        "updated_characters": {"Дима": "У чемодана", "Даша": "Рядом"}
    }

    mock_resp = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = json.dumps(fake_json)
    mock_resp.choices = [mock_choice]

    with patch.object(narrator.client.chat.completions, "create", new=AsyncMock(return_value=mock_resp)):
        pending = await Repository.get_pending_suggestions(limit=5)
        res = await narrator.generate_next_episode(suggestions_to_use=pending)
        assert res["title"] == "Случай у Оккервиля"

        # Verify id1 is 'used'
        async with (await import_conn()) as conn:
            c1 = await conn.execute("SELECT status FROM viewer_suggestions WHERE id = ?", (id1,))
            row1 = await c1.fetchone()
            assert row1[0] == "used"

            # Verify id2 is 'rejected'
            c2 = await conn.execute("SELECT status FROM viewer_suggestions WHERE id = ?", (id2,))
            row2 = await c2.fetchone()
            assert row2[0] == "rejected"

            # Verify id3 is still 'pending'
            c3 = await conn.execute("SELECT status FROM viewer_suggestions WHERE id = ?", (id3,))
            row3 = await c3.fetchone()
            assert row3[0] == "pending"

    if os.path.exists("data/test_triage.db"):
        try:
            os.remove("data/test_triage.db")
        except Exception:
            pass


async def import_conn():
    from database.db import get_db_connection
    return get_db_connection()


@pytest.mark.asyncio
async def test_comment_agent_autonomous_acceptance_and_rejection(monkeypatch):
    from database.db import init_db
    from database.repository import Repository
    monkeypatch.setattr("config.settings.DB_PATH", "data/test_agent_triage.db")
    import os
    if os.path.exists("data/test_agent_triage.db"):
        os.remove("data/test_agent_triage.db")
    await init_db()

    agent = CommentAgent()

    # 1. Accepted suggestion
    resp_accepted = {
        "should_reply": True,
        "reply_text": "Отличная мысль, беру в сценарий!",
        "is_story_suggestion": True,
        "extracted_suggestion": "Дима находит советский раритет в подвале",
        "is_accepted": True,
        "rejection_reason": ""
    }
    mock_resp1 = MagicMock()
    mock_c1 = MagicMock()
    mock_c1.message.content = json.dumps(resp_accepted)
    mock_resp1.choices = [mock_c1]

    with patch.object(agent.client.chat.completions, "create", new=AsyncMock(return_value=mock_resp1)):
        reply1 = await agent.process_comment(
            chat_id="123",
            message_id=1,
            author_name="Алексей",
            author_username="alex",
            comment_text="Пусть Дима найдет советский раритет в подвале"
        )
        assert reply1 == "Отличная мысль, беру в сценарий!"

    # 2. Rejected suggestion (breaks plot)
    resp_rejected = {
        "should_reply": True,
        "reply_text": "Не, если Диму убьет метеорит, сериал сразу закончится :)",
        "is_story_suggestion": True,
        "extracted_suggestion": "Диму сразу убивает метеорит",
        "is_accepted": False,
        "rejection_reason": "Ломает сюжет и убивает главного героя"
    }
    mock_resp2 = MagicMock()
    mock_c2 = MagicMock()
    mock_c2.message.content = json.dumps(resp_rejected)
    mock_resp2.choices = [mock_c2]

    with patch.object(agent.client.chat.completions, "create", new=AsyncMock(return_value=mock_resp2)):
        reply2 = await agent.process_comment(
            chat_id="123",
            message_id=2,
            author_name="Тролль",
            author_username="troll",
            comment_text="Пусть Диму убьет метеорит"
        )
        assert "метеорит" in reply2

    # Check database statuses
    async with (await import_conn()) as conn:
        cursor = await conn.execute("SELECT extracted_idea, status FROM viewer_suggestions ORDER BY id ASC;")
        rows = await cursor.fetchall()
        assert len(rows) == 2
        assert rows[0][0] == "Дима находит советский раритет в подвале"
        assert rows[0][1] == "approved"
        assert rows[1][0] == "Диму сразу убивает метеорит"
        assert rows[1][1] == "rejected"

    # get_pending_suggestions should only return approved (and pending), not rejected
    pending = await Repository.get_pending_suggestions()
    assert len(pending) == 1
    assert pending[0]["extracted_idea"] == "Дима находит советский раритет в подвале"
    assert pending[0]["status"] == "approved"

    if os.path.exists("data/test_agent_triage.db"):
        try:
            os.remove("data/test_agent_triage.db")
        except Exception:
            pass

