import pytest
import os
from unittest.mock import AsyncMock, patch, MagicMock
from config import settings
from database.db import init_db
from database.repository import Repository
from services.comment_agent import CommentAgent
from scheduler.episode_job import run_episode_cycle

TEST_DB_PATH = "data/test_flow.db"


@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", TEST_DB_PATH)
    monkeypatch.setattr(settings, "CHANNEL_ID", "-100777")
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)

    import asyncio
    asyncio.run(init_db())
    yield

    if os.path.exists(TEST_DB_PATH):
        try:
            os.remove(TEST_DB_PATH)
        except Exception:
            pass


@pytest.mark.asyncio
async def test_comment_agent_end_to_end_mock():
    agent = CommentAgent()

    mock_chat_completion = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = (
        '{"should_reply": true, "reply_text": "Диспетчер: Следите за лифтом.", '
        '"is_story_suggestion": true, "extracted_suggestion": "Поставить камеру на 27 этаже"}'
    )
    mock_chat_completion.choices = [mock_choice]

    with patch.object(agent.client.chat.completions, "create", new=AsyncMock(return_value=mock_chat_completion)):
        reply = await agent.process_comment(
            chat_id="-100999",
            message_id=404,
            author_name="Сергей",
            author_username="sergey_kudrovo",
            comment_text="А давайте поставим камеру на 27 этаже?",
            episode_context="Серия 1"
        )

        assert reply == "Диспетчер: Следите за лифтом."

        # Verify suggestion stored in DB
        suggestions = await Repository.get_pending_suggestions()
        assert len(suggestions) == 1
        assert suggestions[0]["extracted_idea"] == "Поставить камеру на 27 этаже"
        assert suggestions[0]["author_username"] == "sergey_kudrovo"

        # Verify duplicate check works
        reply2 = await agent.process_comment(
            chat_id="-100999",
            message_id=404,
            author_name="Сергей",
            author_username="sergey_kudrovo",
            comment_text="А давайте поставим камеру на 27 этаже?",
            episode_context="Серия 1"
        )
        assert reply2 is None  # Skipped because already replied


@pytest.mark.asyncio
async def test_run_episode_cycle_end_to_end():
    # 1. Add a suggestion
    await Repository.add_suggestion(
        raw_comment="Пусть свет мигает в такт азбуке Морзе",
        extracted_idea="Свет мигает азбукой Морзе",
        author_username="morse_guy"
    )

    # 2. Mock Bot and CodeCraft API
    bot = AsyncMock()

    # Mock send_message
    msg_mock = MagicMock()
    msg_mock.message_id = 1001
    bot.send_message.return_value = msg_mock

    # Mock send_poll
    poll_msg_mock = MagicMock()
    poll_msg_mock.message_id = 1002
    poll_mock = MagicMock()
    poll_mock.id = "tg_poll_999"
    poll_msg_mock.poll = poll_mock
    bot.send_poll.return_value = poll_msg_mock

    fake_episode_json = {
        "title": "Морзе на 14 секции",
        "screenplay": "ИНТ. КОРИДОР - НОЧЬ\nЛампочки мигают ритмично...",
        "cliffhanger": "Кто-то постучал в дверь изнутри пустой квартиры.",
        "poll_question": "Открыть дверь?",
        "poll_options": ["Да, открыть", "Убегать"],
        "updated_synopsis": "Артём разгадал сигнал Морзе.",
        "new_mysteries": ["Кто в пустой квартире?"],
        "updated_characters": {"Артём": "У запертой двери"}
    }

    mock_llm_response = MagicMock()
    mock_choice = MagicMock()
    mock_choice.message.content = f"```json\n{import_json_dumps(fake_episode_json)}\n```"
    mock_llm_response.choices = [mock_choice]

    with patch("services.narrator.AsyncOpenAI") as mock_openai_cls:
        mock_instance = MagicMock()
        mock_instance.chat.completions.create = AsyncMock(return_value=mock_llm_response)
        mock_openai_cls.return_value = mock_instance

        ep_id = await run_episode_cycle(bot)
        assert ep_id is not None

        # Verify episode in DB
        ep = await Repository.get_latest_episode(season=1)
        assert ep["title"] == "Морзе на 14 секции"
        assert ep["episode_number"] == 1

        # Verify story state updated
        state = await Repository.get_story_state(season=1)
        assert state["current_episode"] == 1
        assert "Морзе" in state["rolling_synopsis"]

        # Verify suggestion marked used
        pending = await Repository.get_pending_suggestions()
        assert len(pending) == 0

        # Verify poll created in DB
        active_poll = await Repository.get_active_poll()
        assert active_poll is not None
        assert active_poll["poll_id"] == "tg_poll_999"
        assert active_poll["question"] == "Открыть дверь?"


def import_json_dumps(obj):
    import json
    return json.dumps(obj, ensure_ascii=False)
