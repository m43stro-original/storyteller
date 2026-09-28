import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional
from config import settings
from database.repository import Repository

logger = logging.getLogger(__name__)


class ContinuityEngine:
    def __init__(self, lore_bible_path: Optional[str] = None, prompt_template_path: Optional[str] = None):
        self.lore_path = Path(lore_bible_path or settings.LORE_BIBLE_PATH)
        self.prompt_template_path = Path(prompt_template_path or settings.STORY_PROMPT_PATH)
        self._lore_cache: Optional[Dict[str, Any]] = None

    def get_lore(self) -> Dict[str, Any]:
        if self._lore_cache is None:
            if self.lore_path.exists():
                with open(self.lore_path, "r", encoding="utf-8") as f:
                    self._lore_cache = json.load(f)
            else:
                self._lore_cache = {
                    "series_title": "Хроники Кудрово",
                    "genre": "Городской кибернуар, абсурдный реализм",
                    "setting": {"world_description": "Кудрово, бесконечные человейники."},
                    "core_characters": [],
                    "writing_style": {"format": "Киносценарный стиль"}
                }
        return self._lore_cache

    def get_story_prompt_template(self) -> str:
        """Read the editable story prompt template from file, with fallback."""
        if self.prompt_template_path.exists():
            try:
                with open(self.prompt_template_path, "r", encoding="utf-8") as f:
                    content = f.read().strip()
                    if content:
                        return content
            except Exception as e:
                logger.error(f"Failed to read prompt template from {self.prompt_template_path}: {e}")

        # Fallback default if file is missing or unreadable
        return """Ты — главный автор интерактивного сериала «{series_title}».
ЖАНР: {genre}.
ЛОГЛАЙН: {logline}

ОПИСАНИЕ МИРА:
{world_description}

КЛЮЧЕВЫЕ ЛОКАЦИИ:
{key_locations}

ГЛАВНЫЕ ПЕРСОНАЖИ:
{core_characters}

ФОРМАТ И СТИЛЬ:
- Кинематографический формат с элементами сценария: живые визуальные описания, диалоги в сценарном стиле (ИМЯ: реплика), без ИНТ./НАТ. и без клиффхэнгеров.
- Длина 3000-5000 символов, высокая насыщенность событиями, живые диалоги и опрос в конце.

ФОРМАТ ОТВЕТА (JSON):
{
  "title": "Название",
  "screenplay": "Художественный текст серии...",
  "cliffhanger": "Краткая заметка: на чём завершилось действие",
  "poll_question": "Вопрос для голосования",
  "poll_options": ["Вариант 1", "Вариант 2"],
  "used_suggestion_ids": [],
  "rejected_suggestion_ids": [],
  "updated_synopsis": "Синопсис",
  "new_mysteries": ["Тайны"],
  "updated_characters": {"Имя": "Статус"}
}"""

    async def build_narrator_prompts(
        self,
        last_poll_result: Optional[Dict[str, Any]] = None,
        suggestions_to_use: Optional[List[Dict[str, Any]]] = None
    ) -> tuple[str, str]:
        """
        Build (system_prompt, user_prompt) to instruct the LLM to write the next episode
        with rock-solid narrative continuity.
        """
        lore = self.get_lore()
        state = await Repository.get_story_state(season=1)
        next_ep_number = state.get("current_episode", 0) + 1
        rolling_synopsis = state.get("rolling_synopsis", "")
        active_mysteries = state.get("active_mysteries", [])
        character_statuses = state.get("character_statuses", {})

        # Fetch the previous episode for immediate continuity
        prev_ep = await Repository.get_latest_episode(season=1)

        # 1. SYSTEM PROMPT: Loaded from external editable file with safe placeholder substitution
        template = self.get_story_prompt_template()
        replacements = {
            "series_title": lore.get("series_title", "Хроники Кудрово"),
            "genre": lore.get("genre", "Кибернуар, абсурдный реализм"),
            "logline": lore.get("logline", ""),
            "world_description": lore.get("setting", {}).get("world_description", ""),
            "key_locations": json.dumps(lore.get("setting", {}).get("key_locations", []), ensure_ascii=False, indent=2),
            "core_characters": json.dumps(lore.get("core_characters", []), ensure_ascii=False, indent=2),
            "next_ep_number": next_ep_number
        }

        system_prompt = template
        for key, val in replacements.items():
            system_prompt = system_prompt.replace(f"{{{key}}}", str(val))

        # 2. USER PROMPT: Context of current state, previous episode, poll outcome, user suggestions
        user_prompt_lines = [
            f"=== ТЕКУЩЕЕ СОСТОЯНИЕ МИРА (СЕЗОН 1, ПЕРЕД СЕРИЕЙ {next_ep_number}) ===",
            f"Общий синопсис предыдущих событий:\n{rolling_synopsis}\n",
            f"Текущие статусы персонажей:\n{json.dumps(character_statuses, ensure_ascii=False, indent=2)}\n",
            f"Активные загадки и сюжетные линии:\n{json.dumps(active_mysteries, ensure_ascii=False, indent=2)}\n",
        ]

        if prev_ep:
            user_prompt_lines.append(f"=== ПРЕДЫДУЩАЯ СЕРИЯ ({prev_ep['episode_number']}: «{prev_ep['title']}») ===")
            user_prompt_lines.append(f"Текст концовки предыдущей серии:\n{prev_ep.get('cliffhanger') or prev_ep['content'][-800:]}\n")

        if last_poll_result:
            winner = last_poll_result.get("winner_option", "Не определен")
            voters = last_poll_result.get("total_voters", 0)
            user_prompt_lines.append(f"=== ИТОГ ЗРИТЕЛЬСКОГО ГОЛОСОВАНИЯ ПРОШЛОЙ СЕРИИ ===")
            user_prompt_lines.append(f"Вопрос опроса: {last_poll_result.get('question')}")
            user_prompt_lines.append(f"ПОБЕДИВШИЙ ВАРИАНТ (выбор зрителей): «{winner}» (голосов: {voters})")
            user_prompt_lines.append("ОБЯЗАТЕЛЬНО: Начало новой серии должно прямо вытекать из этого решения зрителей!\n")
        elif next_ep_number == 1:
            user_prompt_lines.append("Это пилотная (первая) серия! Познакомь зрителей с главными героями, атмосферой и завяжи первую интригу сериала.\n")

        if suggestions_to_use:
            user_prompt_lines.append("=== ОДОБРЕННЫЕ ИДЕИ ЗРИТЕЛЕЙ ИЗ КОММЕНТАРИЕВ (ОБЯЗАТЕЛЬНО К ИСПОЛЬЗОВАНИЮ) ===")
            user_prompt_lines.append(
                "Эти идеи зрителей прошли отбор и были ОДОБРЕНЫ к включению в сюжет. "
                "Ты ОБЯЗАН гарантированно использовать и органично вплести их в сюжет этой серии. "
                "Если идей несколько — используй их все (вплети в разные сцены или объедини в общем действии серии).\n"
                "Список одобренных идей:"
            )
            for s in suggestions_to_use:
                sid = s.get("id")
                author = s.get("author_username") or s.get("author_name") or "Зритель"
                idea = s.get("extracted_idea") or s.get("raw_comment")
                id_prefix = f"[ID: {sid}] " if sid is not None else ""
                user_prompt_lines.append(f"- {id_prefix}От @{author}: {idea}")
            user_prompt_lines.append(
                "Укажи в ответе:\n"
                "- 'used_suggestion_ids': [список ID идей, которые ты использовал в серии],\n"
                "- 'rejected_suggestion_ids': [список ID идей, если какую-то идею технически невозможно было включить, иначе пустой список []].\n"
            )

        user_prompt_lines.append(f"Напиши Серию {next_ep_number}. Выдай ответ строго в формате JSON.")

        user_prompt = "\n".join(user_prompt_lines)
        return system_prompt, user_prompt
