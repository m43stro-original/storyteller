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


@pytest.mark.asyncio
async def test_continuity_engine_season_chronicle_and_1987_invariants():
    # Insert multiple episodes
    await Repository.add_episode(
        season=1,
        episode_number=1,
        title="Пилот",
        content="Начало истории в Кудрово...",
        cliffhanger="Провалились в котлован."
    )
    await Repository.add_episode(
        season=1,
        episode_number=2,
        title="Тени 1987-го",
        content="Герои вылезают в 1987 году...",
        cliffhanger="Встретили пионера Мишу."
    )

    engine = ContinuityEngine()
    sys_prompt, user_prompt = await engine.build_narrator_prompts()

    # 1. Verify 1987 USSR invariants
    assert "1987 ГОД" in user_prompt or "1987" in user_prompt
    assert "СССР" in user_prompt
    assert "АНАХРОНИЗМ" in user_prompt

    # 2. Verify compact season chronicle is present
    assert "КРАТКАЯ ХРОНИКА ВСЕХ СЕРИЙ" in user_prompt
    assert "Серия 1: «Пилот»" in user_prompt
    assert "Серия 2: «Тени 1987-го»" in user_prompt

    # 3. Verify full previous episode text is included
    assert "Герои вылезают в 1987 году..." in user_prompt


@pytest.mark.asyncio
async def test_continuity_engine_episode_14_finale():
    # Set current episode to 13, so next is 14 (Finale)
    await Repository.update_story_state(
        season=1,
        current_episode=13,
        rolling_synopsis="Могний вылит на осколок звезды.",
        active_mysteries=["Схлопнется ли звезда?"],
        character_statuses={"Дима": "У котлована"}
    )
    await Repository.add_episode(
        season=1,
        episode_number=13,
        title="Рассвет Могния",
        content="Осколок залит могнием...",
        cliffhanger="Участковый требует поднять руки."
    )

    engine = ContinuityEngine()
    sys_prompt, user_prompt = await engine.build_narrator_prompts(
        last_poll_result={"question": "Как поступить?", "winner_option": "Отвлечь милицию", "total_voters": 42}
    )

    # Verify system prompt mentions Season 1 Finale and no poll
    assert "ФИНАЛ" in sys_prompt
    assert "is_finale" in sys_prompt

    # Verify user prompt injects Episode 14 Finale instructions
    assert "СЕРИЯ 14 — ГРАНДИОЗНЫЙ И ЭПИЧНЫЙ ФИНАЛ 1-ГО СЕЗОНА" in user_prompt
    assert "СХЛОПЫВАНИЕ ЗВЕЗДЫ" in user_prompt
    assert "ПРОЩАНИЕ С 1987 ГОДОМ" in user_prompt
    assert "КАТЕГОРИЧЕСКИ БЕЗ ОПРОСА" in user_prompt
    assert "is_finale" in user_prompt
    assert "poll_question': null" in user_prompt
    assert "poll_options': []" in user_prompt


