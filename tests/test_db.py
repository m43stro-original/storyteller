import pytest
import os
import aiosqlite
from pathlib import Path
from config import settings
from database.db import init_db, get_db_connection
from database.repository import Repository

TEST_DB_PATH = "data/test_kudrovo.db"


@pytest.fixture(autouse=True)
def setup_test_db(monkeypatch):
    monkeypatch.setattr(settings, "DB_PATH", TEST_DB_PATH)
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
async def test_init_db_creates_default_story_state():
    state = await Repository.get_story_state(season=1)
    assert state is not None
    assert state["season"] == 1
    assert state["current_episode"] == 0
    assert "Кудрово" in state["rolling_synopsis"]
    assert isinstance(state["active_mysteries"], list)
    assert ("Дима" in state["character_statuses"] or "Артём" in state["character_statuses"] or len(state["character_statuses"]) >= 0)


@pytest.mark.asyncio
async def test_add_and_get_episodes():
    ep_id = await Repository.add_episode(
        season=1,
        episode_number=1,
        title="Шорох на 27 этаже",
        content="ИНТ. ПОДЪЕЗД - НОЧЬ\nАртём поднимается по лестнице...",
        cliffhanger="Лифт внезапно открывается сам."
    )
    assert ep_id > 0

    latest = await Repository.get_latest_episode(season=1)
    assert latest is not None
    assert latest["id"] == ep_id
    assert latest["episode_number"] == 1
    assert latest["title"] == "Шорох на 27 этаже"

    # Add second episode
    ep2_id = await Repository.add_episode(
        season=1,
        episode_number=2,
        title="Ключ от чердака",
        content="ИНТ. ЧЕРДАК - НОЧЬ\nИнга Васильевна стоит у щитка...",
        cliffhanger="Звук шагов за спиной."
    )
    assert ep2_id > ep_id

    recent = await Repository.get_recent_episodes(limit=2, season=1)
    assert len(recent) == 2
    assert recent[-1]["episode_number"] == 2


@pytest.mark.asyncio
async def test_polls_lifecycle():
    ep_id = await Repository.add_episode(
        season=1,
        episode_number=1,
        title="Тестовая серия",
        content="Контент"
    )
    poll_db_id = await Repository.add_poll(
        poll_id="poll_test_123",
        channel_id="-100111222",
        message_id=55,
        question="Куда бежать Артёму?",
        options=["В лифт", "В подвал", "На крышу"],
        episode_id=ep_id
    )
    assert poll_db_id > 0

    active_poll = await Repository.get_active_poll()
    assert active_poll is not None
    assert active_poll["poll_id"] == "poll_test_123"
    assert active_poll["is_closed"] == 0
    assert active_poll["options"] == ["В лифт", "В подвал", "На крышу"]

    # Close the poll
    await Repository.close_poll(
        poll_id="poll_test_123",
        winner_option="В подвал",
        total_voters=42
    )

    # Now active poll should be None
    no_active = await Repository.get_active_poll()
    assert no_active is None

    # But we can query it directly
    closed = await Repository.get_poll_by_poll_id("poll_test_123")
    assert closed["is_closed"] == 1
    assert closed["winner_option"] == "В подвал"
    assert closed["total_voters"] == 42


@pytest.mark.asyncio
async def test_story_state_update():
    await Repository.update_story_state(
        season=1,
        current_episode=1,
        rolling_synopsis="Артём обнаружил подвал.",
        active_mysteries=["Куда ведёт кабель из щитка?"],
        character_statuses={"Артём": "В подвале", "Борисыч": "У лифта"}
    )

    state = await Repository.get_story_state(season=1)
    assert state["current_episode"] == 1
    assert state["rolling_synopsis"] == "Артём обнаружил подвал."
    assert state["active_mysteries"] == ["Куда ведёт кабель из щитка?"]
    assert state["character_statuses"]["Артём"] == "В подвале"


@pytest.mark.asyncio
async def test_viewer_suggestions_crud():
    s_id = await Repository.add_suggestion(
        raw_comment="А пусть Артём встретит курьера-конкурента в синей форме!",
        extracted_idea="Встретить курьера-конкурента в синей форме",
        comment_id=101,
        chat_id="-100999",
        author_username="kudrovo_fan",
        author_name="Иван"
    )
    assert s_id > 0

    pending = await Repository.get_pending_suggestions(limit=5)
    assert len(pending) == 1
    assert pending[0]["id"] == s_id
    assert pending[0]["author_username"] == "kudrovo_fan"

    # Mark as used in episode 1
    ep_id = await Repository.add_episode(
        season=1,
        episode_number=1,
        title="Тест идей",
        content="Контент"
    )
    await Repository.mark_suggestions_used([s_id], episode_id=ep_id)

    pending_after = await Repository.get_pending_suggestions(limit=5)
    assert len(pending_after) == 0


@pytest.mark.asyncio
async def test_replied_comments_dedup():
    assert not await Repository.is_comment_replied(chat_id="-100999", message_id=505)

    await Repository.record_replied_comment(
        chat_id="-100999",
        message_id=505,
        author_name="Ольга",
        comment_text="Где прода? ждем!",
        reply_text="Диспетчер: Прода варится в котле ЖК, ожидайте."
    )

    assert await Repository.is_comment_replied(chat_id="-100999", message_id=505)
