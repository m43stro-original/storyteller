import json
import re
import logging
from typing import Dict, Any, List, Optional
from pathlib import Path
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

from config import settings
from database.repository import Repository
from services.continuity import ContinuityEngine

logger = logging.getLogger(__name__)


class NarratorService:
    def __init__(self, continuity_engine: Optional[ContinuityEngine] = None):
        self.continuity = continuity_engine or ContinuityEngine()
        self.client = AsyncOpenAI(
            base_url=settings.CODECRAFT_BASE_URL,
            api_key=settings.CODECRAFT_API_KEY or "dummy-key-for-init"
        )
        self.model = settings.CODECRAFT_MODEL

    def _clean_json_output(self, raw_text: str) -> Dict[str, Any]:
        """Strip markdown json fencing and parse JSON safely."""
        text = raw_text.strip()
        # Remove ```json and ``` if present
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        text = text.strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Fallback regex search for outer {...}
            match = re.search(r"(\{.*\})", text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            raise ValueError(f"Failed to parse JSON response from narrator LLM: {raw_text[:200]}...")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=3, max=20),
        reraise=True
    )
    async def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        logger.info(f"Calling CodeCraft API ({self.model}) for episode generation...")
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.85,
            response_format={"type": "json_object"}
        )
        content = response.choices[0].message.content or ""
        return content

    async def generate_next_episode(
        self,
        last_poll_result: Optional[Dict[str, Any]] = None,
        suggestions_to_use: Optional[List[Dict[str, Any]]] = None
    ) -> Dict[str, Any]:
        """
        Generate the next episode, save it to the database, update story state,
        and return the full episode payload.
        """
        system_prompt, user_prompt = await self.continuity.build_narrator_prompts(
            last_poll_result=last_poll_result,
            suggestions_to_use=suggestions_to_use
        )

        raw_output = await self._call_llm(system_prompt, user_prompt)
        parsed = self._clean_json_output(raw_output)

        title = parsed.get("title", "Новая глава")
        screenplay = parsed.get("screenplay", "")
        cliffhanger = parsed.get("cliffhanger", "")
        poll_question = parsed.get("poll_question", "Что делать дальше?")
        poll_options = parsed.get("poll_options", ["Продолжить расследование", "Отступить"])

        # Normalize poll options (Telegram max 10 options, each <= 100 chars, minimum 2 options)
        clean_options = [str(opt)[:100] for opt in poll_options if str(opt).strip()]
        if len(clean_options) < 2:
            clean_options = ["Исследовать дальше", "Вернуться в штаб"]
        clean_options = clean_options[:5]

        # Update story state in database
        state = await Repository.get_story_state(season=1)
        next_ep_number = state.get("current_episode", 0) + 1

        updated_synopsis = parsed.get("updated_synopsis") or state.get("rolling_synopsis", "")
        new_mysteries = parsed.get("new_mysteries") or state.get("active_mysteries", [])
        updated_characters = parsed.get("updated_characters") or state.get("character_statuses", {})

        await Repository.update_story_state(
            season=1,
            current_episode=next_ep_number,
            rolling_synopsis=updated_synopsis,
            active_mysteries=new_mysteries,
            character_statuses=updated_characters
        )

        # Sync characters into lore_bible.json so it stays in sync
        self._sync_characters_to_lore_bible(updated_characters)

        # Save episode to DB
        episode_id = await Repository.add_episode(
            season=1,
            episode_number=next_ep_number,
            title=title,
            content=screenplay,
            cliffhanger=cliffhanger
        )

        # Process viewer suggestions chosen by LLM
        valid_pending_ids = {s["id"] for s in suggestions_to_use or [] if "id" in s}

        if "used_suggestion_ids" in parsed or "rejected_suggestion_ids" in parsed:
            raw_used = parsed.get("used_suggestion_ids", [])
            if not isinstance(raw_used, list):
                raw_used = []
            actually_used_ids = [int(x) for x in raw_used if str(x).isdigit() and int(x) in valid_pending_ids]

            raw_rejected = parsed.get("rejected_suggestion_ids", [])
            if not isinstance(raw_rejected, list):
                raw_rejected = []
            actually_rejected_ids = [
                int(x) for x in raw_rejected
                if str(x).isdigit() and int(x) in valid_pending_ids and int(x) not in actually_used_ids
            ]

            if actually_used_ids:
                logger.info(f"Narrator accepted viewer suggestion IDs: {actually_used_ids}")
                await Repository.mark_suggestions_used(actually_used_ids, episode_id=episode_id)

            for rid in actually_rejected_ids:
                logger.info(f"Narrator rejected nonsense viewer suggestion ID: {rid}")
                await Repository.update_suggestion_status(rid, status="rejected")
        else:
            # Fallback / backwards compatibility: mark all passed suggestions as used
            if suggestions_to_use:
                used_ids = [s["id"] for s in suggestions_to_use if "id" in s]
                await Repository.mark_suggestions_used(used_ids, episode_id=episode_id)

        return {
            "episode_id": episode_id,
            "season": 1,
            "episode_number": next_ep_number,
            "title": title,
            "screenplay": screenplay,
            "cliffhanger": cliffhanger,
            "poll_question": poll_question,
            "poll_options": clean_options
        }

    def _sync_characters_to_lore_bible(self, updated_characters: Dict[str, Any]) -> None:
        try:
            lore_file = Path(settings.LORE_BIBLE_PATH)
            if not lore_file.exists():
                return
            with open(lore_file, "r", encoding="utf-8") as f:
                lore = json.load(f)

            core_chars = lore.get("core_characters", [])
            existing_names = {c.get("name") for c in core_chars if "name" in c}

            changed = False
            for char_name, status_or_desc in (updated_characters or {}).items():
                if char_name and char_name not in existing_names:
                    core_chars.append({
                        "name": char_name,
                        "role": "Персонаж сериала",
                        "traits": str(status_or_desc)
                    })
                    existing_names.add(char_name)
                    changed = True

            if changed:
                lore["core_characters"] = core_chars
                with open(lore_file, "w", encoding="utf-8") as f:
                    json.dump(lore, f, ensure_ascii=False, indent=2)
                if hasattr(self.continuity, "_lore_cache"):
                    self.continuity._lore_cache = None
                logger.info(f"Synchronized new characters to lore_bible.json: {existing_names}")
        except Exception as e:
            logger.warning(f"Failed to sync characters to lore_bible.json: {e}")

