import html
import re
import logging
from aiogram import Router, types
from aiogram.filters import Command
from config import settings
from database.repository import Repository
from scheduler.episode_job import run_episode_cycle

logger = logging.getLogger(__name__)
admin_router = Router(name="admin_router")


def is_admin(user_id: int) -> bool:
    if not settings.ADMIN_IDS:
        return True  # If no admins configured, allow for setup
    return user_id in settings.ADMIN_IDS


async def safe_send(message: types.Message, html_text: str):
    """Safely send message with HTML, falling back to plain text if Telegram rejects parsing."""
    try:
        await message.answer(html_text, parse_mode="HTML")
    except Exception as e:
        logger.warning(f"Failed to send HTML formatted message ({e}), falling back to plain text...")
        plain_text = re.sub(r"<[^>]+>", "", html_text)
        try:
            await message.answer(plain_text, parse_mode=None)
        except Exception as e2:
            logger.error(f"Failed to send fallback plain text message: {e2}")


@admin_router.message(Command("start", "help"))
async def cmd_start(message: types.Message):
    if not is_admin(message.from_user.id):
        await message.reply("Привет! Этот бот ведет интерактивный сериал. Читайте серии в нашем канале!")
        return

    text = (
        "🏢 <b>Панель управления интерактивным сериалом</b>\n\n"
        "Доступные команды:\n"
        "🔹 <code>/status</code> — текущее состояние мира, номер серии и активный опрос\n"
        "🔹 <code>/post_now</code> — принудительно выпустить следующую серию в канал\n"
        "🔹 <code>/preview</code> — сгенерировать серию и прислать сюда в ЛС (без публикации в канал)\n"
        "🔹 <code>/ideas</code> — список сюжетных идей из комментариев зрителей\n"
        "🔹 <code>/approve &lt;id&gt;</code> — утвердить идею зрителя в приоритет\n"
        "🔹 <code>/reject &lt;id&gt;</code> — отклонить идею зрителя\n"
    )
    await safe_send(message, text)


@admin_router.message(Command("status"))
async def cmd_status(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    try:
        state = await Repository.get_story_state(season=1)
        active_poll = await Repository.get_active_poll()
        pending_ideas = await Repository.get_pending_suggestions(limit=10)
        latest_ep = await Repository.get_latest_episode(season=1)
        season_episodes = await Repository.get_season_episodes(season=state.get("season", 1))

        poll_info = "Нет активного опроса"
        if active_poll:
            opts_str = ", ".join(f"«{o}»" for o in active_poll.get("options", []))
            poll_info = f"«{active_poll['question']}» (Варианты: {opts_str})"

        last_ep_title = f"Серия {latest_ep['episode_number']}: «{latest_ep['title']}»" if latest_ep else "Еще не выпущено"

        chronology_lines = []
        if season_episodes:
            for ep in season_episodes:
                num = ep.get("episode_number")
                title = html.escape(ep.get("title", ""))
                cliff = ep.get("cliffhanger", "").strip()
                cliff_snip = f" — <i>{html.escape(cliff[:85])}...</i>" if cliff else ""
                chronology_lines.append(f"• <b>Серия {num}:</b> «{title}»{cliff_snip}")
            chronology_block = "\n".join(chronology_lines)
        else:
            chronology_block = "<i>Серий пока нет</i>"

        text = (
            f"📊 <b>СТАТУС СЕРИАЛА (СЕЗОН {state.get('season', 1)})</b>\n\n"
            f"• Эпоха: <b>СССР, 1987 год</b>\n"
            f"• Текущая серия: {state.get('current_episode', 0)}\n"
            f"• Последний релиз: {html.escape(last_ep_title)}\n"
            f"• Активный опрос: {html.escape(poll_info)}\n"
            f"• Идей в очереди: {len(pending_ideas)}\n\n"
            f"📜 <b>Хронология сезона:</b>\n{chronology_block}\n\n"
            f"📖 <b>Актуальный синопсис:</b>\n<i>{html.escape(state.get('rolling_synopsis', '')[:400])}...</i>\n\n"
            f"⚙️ <b>Конфигурация:</b>\n"
            f"• Narrator API: <code>{settings.CODECRAFT_BASE_URL}</code> ({settings.CODECRAFT_MODEL})\n"
            f"• Comments API: <code>{settings.GROQ_BASE_URL}</code> ({settings.GROQ_MODEL})\n"
            f"• Канал: <code>{settings.CHANNEL_ID}</code>\n"
            f"• Чат обсуждений: <code>{settings.DISCUSSION_CHAT_ID}</code>\n"
            f"• Расписание: <code>{settings.SCHEDULE_CRON}</code>"
        )
        await safe_send(message, text)
    except Exception as e:
        logger.error(f"Error in cmd_status: {e}", exc_info=True)
        await message.reply(f"❌ Ошибка получения статуса: {e}")


@admin_router.message(Command("post_now"))
async def cmd_post_now(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    await message.reply("⏳ Запускаю цикл генерации и публикации серии... Это займет 15-30 секунд.")
    try:
        ep_id = await run_episode_cycle(message.bot)
        if ep_id:
            await message.answer(f"🚀 Серия #{ep_id} успешно сгенерирована и отправлена в канал!")
        else:
            await message.answer("❌ Произошла ошибка при публикации серии. Проверьте логи.")
    except Exception as e:
        logger.error(f"Error in cmd_post_now: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка при публикации серии:\n{e}")


@admin_router.message(Command("preview"))
async def cmd_preview(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    await message.reply("⏳ Генерирую черновик серии для предпросмотра...")
    try:
        ep_id = await run_episode_cycle(message.bot, dry_run_chat_id=message.chat.id)
        if ep_id:
            await message.answer("✅ Черновик серии отправлен выше. (Активный опрос канала не закрывался).")
        else:
            await message.answer("❌ Ошибка при генерации черновика.")
    except Exception as e:
        logger.error(f"Error in cmd_preview: {e}", exc_info=True)
        await message.answer(f"❌ Ошибка генерации черновика:\n{e}")


@admin_router.message(Command("ideas"))
async def cmd_ideas(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    try:
        ideas = await Repository.get_pending_suggestions(limit=10)
        if not ideas:
            await message.answer("Пока нет новых сюжетных предложений от зрителей.")
            return

        lines = ["💡 <b>Свежие идеи от зрителей:</b>\n"]
        for item in ideas:
            raw_author = item.get("author_username") or item.get("author_name") or "Аноним"
            author = html.escape(str(raw_author))
            status = html.escape(str(item.get("status", "pending")))
            idea = html.escape(str(item.get("extracted_idea") or item.get("raw_comment") or ""))
            lines.append(f"• <code>[ID {item['id']}]</code> <b>@{author}</b> ({status}):\n  <i>{idea}</i>")

        lines.append("\nДля управления: <code>/approve &lt;id&gt;</code> или <code>/reject &lt;id&gt;</code>")
        await safe_send(message, "\n".join(lines))
    except Exception as e:
        logger.error(f"Error in cmd_ideas: {e}", exc_info=True)
        await message.reply(f"❌ Ошибка вывода идей: {e}")


@admin_router.message(Command("approve"))
async def cmd_approve(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply("Использование: <code>/approve &lt;id&gt;</code>", parse_mode="HTML")
        return

    idea_id = int(parts[1])
    await Repository.update_suggestion_status(idea_id, "approved")
    await message.reply(f"✅ Идея ID {idea_id} одобрена и будет приоритетно использована в следующей серии.")


@admin_router.message(Command("reject"))
async def cmd_reject(message: types.Message):
    if not is_admin(message.from_user.id):
        return

    parts = message.text.split()
    if len(parts) < 2 or not parts[1].isdigit():
        await message.reply("Использование: <code>/reject &lt;id&gt;</code>", parse_mode="HTML")
        return

    idea_id = int(parts[1])
    await Repository.update_suggestion_status(idea_id, "rejected")
    await message.reply(f"🚫 Идея ID {idea_id} отклонена.")
