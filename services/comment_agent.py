import json
import re
import logging
from typing import Dict, Any, Optional
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config import settings
from database.repository import Repository

logger = logging.getLogger(__name__)


class CommentAgent:
    def __init__(self):
        self.client = AsyncOpenAI(
            base_url=settings.GROQ_BASE_URL,
            api_key=settings.GROQ_API_KEY or "dummy-key-for-init"
        )
        self.model = settings.GROQ_MODEL

    def _clean_json_output(self, raw_text: str) -> Dict[str, Any]:
        text = raw_text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"(\{.*\})", text, re.DOTALL)
            if match:
                return json.loads(match.group(1))
            raise ValueError(f"Failed to parse JSON response from Groq comment agent: {raw_text[:200]}")

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1.5, min=2, max=10),
        reraise=True
    )
    async def _evaluate_comment(
        self,
        comment_text: str,
        author_name: str,
        episode_context: str,
        characters_lore: Optional[str] = None,
        characters_status: Optional[str] = None,
        story_synopsis: Optional[str] = None
    ) -> Dict[str, Any]:
        system_prompt = """Ты — Сценарист интерактивного фанфик-сериала про Диму и Дашу. Ты общаешься со зрителями в чате сериала.
 
СЕТТИНГ И ЭПОХА:
Действие сериала прямо сейчас происходит в СССР, 1987 ГОД (Ленинград и совхозная окраина). Главные герои из 2026 года провалились в прошлое через аномалию. Строжайший запрет анахронизмов: в 1987 году нет интернета, сотовой связи, смартфонов, стримов, ютуба, электросамокатов. Местные жители живут советским бытом.

ТВОИ ПРАВИЛА И ПОВЕДЕНИЕ:
1. НЕ ВЛЕЗАЙ В РАЗГОВОРЫ, СПОРЫ И ПЕРЕПАЛКИ ЗРИТЕЛЕЙ ДРУГ С ДРУГОМ! Если зрители спорят между собой, обсуждают что-то постороннее или просто общаются друг с другом — СТРОГО ставь `should_reply: false`.
2. Если зритель просто реагирует на серию эмоциями (смех, «ахаха», «имба», «жесть», «лол», эмодзи, короткие восклицания) без прямого вопроса к автору и без предложения по сюжету — ставь `should_reply: false`. Не нужно отвечать на каждую реплику и спамить в чат.
3. ОТВЕЧАЙ (`should_reply: true`) ТОЛЬКО ТОГДА, КОГДА:
   - Зритель прямо обращается к тебе (сценаристу/автору/боту) с вопросом о происходящем, о персонажах или о сериале. Отвечай в каноне, опираясь на лор персонажей и текущий сюжет 1987 года.
   - Зритель предлагает сюжетную идею, поворот или твист.
4. СТРОГИЙ ФИЛЬТР И ОТБОР СЮЖЕТНЫХ ИДЕЙ ЗРИТЕЛЕЙ:
   Сериал — это связная история про реальных людей в антураже СССР 1987 года. НЕ превращай сериал в цирк шизофрении и помойку глупых мемов!

   - `is_story_suggestion`: true ТОЛЬКО если зритель предлагает реальное сюжетное событие, поворот или персонажа. Если зритель просто просит вставить отдельное бессмысленное слово/фразу («скажи БАНАН_777», «крикни скибиди»), спамит или троллит — ставь false и пустую строку "".

   - `is_accepted`: ты — строгий, но справедливый главный редактор и сценарист:
     * ПРИНИМАЙ (`is_accepted: true`) ТОЛЬКО ТО, ЧТО РЕАЛЬНО УЛУЧШАЕТ СЮЖЕТ:
       - Осмысленные сюжетные ходы: тайные встречи, приключения в Кудрово, ревность, конфликты, детективные расследования, бытовые передряги, смешные или романтические ситуации между героями.
       - Адекватные второстепенные персонажи (бабушка Даши, подозрительный сосед, вахтёр, друг из спортзала, мэр района).
       - Повороты сюжета, которые логичны и развивают историю, пусть даже они неожиданные.

     * СТРОГО ОТКЛОНЯЙ (`is_accepted: false`) ЛЮБУЮ ОТКРОВЕННУЮ ДИЧЬ И МУСОР:
       - Шизофазия и бессмысленные предметы/существа (всякие «биг пенсилы», живые ручки, мутанты, бред сумасшедшего).
       - Мемный спам и навязывание кодовых слов («пусть скажет БАНАН_777», «пусть крикнет скибиди»).
       - Пошлые, туалетные и кринжовые намёки школьников.
       - Ломающее сеттинг и логику мира: пришельцы, лазеры, магия, метеориты, внезапная смерть Димы или Даши.
       - Бессвязная ерунда, из которой невозможно построить интересную кинематографичную сцену.
     * `rejection_reason`: кратко укажи, почему идея забракована (например: "бессмысленный мемный спам", "пошлая дичь / шизофазия", "ломает сюжет").

   - ТЕКСТ ОТВЕТА (`reply_text`):
     * Если идея ПРИНЯТА (`is_accepted: true`): ответь дружелюбно и живо, подтвердив, что ход стоящий и пойдёт в сюжет.
     * Если идея ОТКЛОНЕНА (`is_accepted: false`): с иронией и лёгким авторским сарказмом остуди пыл комментатора (например: «Не, ребят, у нас тут всё-таки связная история, а не генератор мемов про биг пенсилы», «Тимофей такое говорить не будет, давайте нормальные сюжетные идеи :)»).

ВЕРНИ ОТВЕТ СТРОГО В JSON:
{
  "should_reply": true/false,
  "reply_text": "текст ответа зрителю от лица сценариста (если should_reply: true, иначе пустая строка)",
  "is_story_suggestion": true/false,
  "extracted_suggestion": "суть идеи или пустая строка",
  "is_accepted": true/false,
  "rejection_reason": "краткая причина отказа или пустая строка"
}
"""

        user_prompt_parts = []
        if characters_lore or characters_status:
            user_prompt_parts.append("=== ЛОР И ПЕРСОНАЖИ СЕРИАЛА ===")
            if characters_lore:
                user_prompt_parts.append(f"Базовые описания героев:\n{characters_lore}")
            if characters_status:
                user_prompt_parts.append(f"Где герои находятся и в каком состоянии сейчас:\n{characters_status}")

        if story_synopsis:
            user_prompt_parts.append(f"=== АКТУАЛЬНЫЙ СИНОПСИС СЮЖЕТА ===\n{story_synopsis}")

        if episode_context:
            user_prompt_parts.append(f"=== КОНТЕКСТ СЕРИИ / СООБЩЕНИЯ ===\n{episode_context}")

        user_prompt_parts.append(f"=== СООБЩЕНИЕ ЗРИТЕЛЯ ===\nАвтор: {author_name}\nТекст: {comment_text}")

        user_prompt = "\n\n".join(user_prompt_parts)

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.75,
            response_format={"type": "json_object"}
        )

        content = response.choices[0].message.content or "{}"
        return self._clean_json_output(content)

    async def process_comment(
        self,
        chat_id: str,
        message_id: int,
        author_name: str,
        author_username: Optional[str],
        comment_text: str,
        episode_context: Optional[str] = None,
        characters_lore: Optional[str] = None,
        characters_status: Optional[str] = None,
        story_synopsis: Optional[str] = None
    ) -> Optional[str]:
        """
        Process a user comment:
        1. Check if already replied to.
        2. Triage via Groq LLM.
        3. If there is a story suggestion, store it in viewer_suggestions.
        4. If should_reply is True, store to replied_comments and return reply_text.
        """
        # Dedup check
        if await Repository.is_comment_replied(str(chat_id), message_id):
            logger.info(f"Comment {message_id} in {chat_id} already replied. Skipping.")
            return None

        context = episode_context or "Интерактивный сериал-фанфик про Диму и Дашу в Кудрово."

        try:
            eval_result = await self._evaluate_comment(
                comment_text=comment_text,
                author_name=author_name,
                episode_context=context,
                characters_lore=characters_lore,
                characters_status=characters_status,
                story_synopsis=story_synopsis
            )
            logger.info(f"🔍 Groq triage результат для msg {message_id}: {eval_result}")
        except Exception as e:
            logger.error(f"❌ Failed to triage comment {message_id} with Groq: {e}", exc_info=True)
            return None

        # Parse booleans safely
        is_suggestion = eval_result.get("is_story_suggestion", False)
        if isinstance(is_suggestion, str):
            is_suggestion = is_suggestion.strip().lower() in ("true", "1", "yes", "да")

        should_reply = eval_result.get("should_reply", True)
        if isinstance(should_reply, str):
            should_reply = should_reply.strip().lower() in ("true", "1", "yes", "да")

        # Check for plot suggestion
        idea = (eval_result.get("extracted_suggestion") or "").strip()
        if is_suggestion and idea:
            is_accepted = eval_result.get("is_accepted", True)
            if isinstance(is_accepted, str):
                is_accepted = is_accepted.strip().lower() in ("true", "1", "yes", "да")
            status = "approved" if is_accepted else "rejected"
            logger.info(f"💡 Extracted viewer suggestion from {author_name}: {idea} (status={status})")
            await Repository.add_suggestion(
                comment_id=message_id,
                chat_id=str(chat_id),
                author_username=author_username,
                author_name=author_name,
                raw_comment=comment_text,
                extracted_idea=idea,
                status=status
            )

        # Check for reply
        reply = (eval_result.get("reply_text") or "").strip()
        if should_reply and reply:
            await Repository.record_replied_comment(
                chat_id=str(chat_id),
                message_id=message_id,
                author_name=author_name,
                comment_text=comment_text,
                reply_text=reply
            )
            return reply

        return None
