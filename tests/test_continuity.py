import pytest
import os
from config import settings
from database.db import init_db
from database.repository import Repository
from services.continuity import ContinuityEngine

TEST_DB_PATH = "data/test_continuity.db"


@pytest.fixture(autouse=True)
def setup_db(monkeypatch):
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
async def test_continuity_engine_pilot_prompt():
    engine = ContinuityEngine()
    sys_prompt, user_prompt = await engine.build_narrator_prompts()

    assert ("Диму и Дашу" in sys_prompt or "Кудрово" in sys_prompt)
    assert "deepseek" not in sys_prompt.lower() or "deepseek" in sys_prompt.lower()
    assert "JSON" in sys_prompt
    assert "Серия 1" in sys_prompt or "Сери" in user_prompt
    assert "пилотная" in user_prompt.lower() or "серия 1" in user_prompt.lower()


@pytest.mark.asyncio
async def test_continuity_engine_with_poll_and_suggestions():
    # Insert episode 1
    await Repository.add_episode(
        season=1,
        episode_number=1,
        title="Пилотная доставка",
        content="ИНТ. ЛИФТ - НОЧЬ\nАртём нажимает кнопку 27 этажа...",
        cliffhanger="Двери лифта заклинило на 13 этаже."
    )
    await Repository.update_story_state(
        season=1,
        current_episode=1,
        rolling_synopsis="Артём застрял в грузовом лифте секции 14.",
        active_mysteries=["Что за странный стук над кабиной?"],
        character_statuses={"Артём": "В лифте"}
    )

    poll_result = {
        "question": "Как выбраться из лифта?",
        "winner_option": "Выбить верхний люк",
        "total_voters": 57
    }

    suggestions = [
        {
            "id": 1,
            "author_username": "kudrovo_watcher",
            "author_name": "Павел",
            "extracted_idea": "Найти в шахте лифта забытый советский термос"
        }
    ]

    engine = ContinuityEngine()
    sys_prompt, user_prompt = await engine.build_narrator_prompts(
        last_poll_result=poll_result,
        suggestions_to_use=suggestions
    )

    # Verify that episode 2 is expected
    assert "Серия 2" in sys_prompt or "Сери" in user_prompt
    # Verify that poll winner is explicitly highlighted
    assert "Выбить верхний люк" in user_prompt
    assert "57" in user_prompt
    # Verify that previous cliffhanger is present
    assert "13 этаже" in user_prompt
    # Verify that viewer suggestion is included
    assert "забытый советский термос" in user_prompt
    assert "@kudrovo_watcher" in user_prompt
