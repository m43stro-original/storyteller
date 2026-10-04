import logging
from typing import Optional, List
from aiogram import Bot
from config import settings
from database.repository import Repository
from services.narrator import NarratorService
from services.poll_service import PollService

logger = logging.getLogger(__name__)


async def send_chunked_message(bot: Bot, chat_id: str | int, text: str) -> int:
    """Send text message, splitting into chunks if longer than Telegram 4096 char limit."""
    max_len = 4000
    if len(text) <= max_len:
        msg = await bot.send_message(chat_id=chat_id, text=text, parse_mode=None)
        return msg.message_id

    # Split by paragraphs or newlines
    paragraphs = text.split("\n\n")
    chunks: List[str] = []
    current_chunk = ""

    for p in paragraphs:
        if len(current_chunk) + len(p) + 2 > max_len:
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
            # If a single paragraph is longer than max_len, split by slice
            while len(p) > max_len:
                chunks.append(p[:max_len])
                p = p[max_len:]
            current_chunk = p
        else:
            current_chunk = f"{current_chunk}\n\n{p}" if current_chunk else p

    if current_chunk:
        chunks.append(current_chunk.strip())

    last_msg_id = 0
    total = len(chunks)
    for idx, chunk in enumerate(chunks, start=1):
        header = f"[{idx}/{total}]\n" if total > 1 else ""
        msg = await bot.send_message(chat_id=chat_id, text=header + chunk, parse_mode=None)
        last_msg_id = msg.message_id

    return last_msg_id


async def run_episode_cycle(bot: Bot, dry_run_chat_id: Optional[int | str] = None) -> Optional[int]:
    """
    Execute full episode cycle:
    1. Close active poll (if any) and get winning choice.
    2. Collect viewer suggestions.
    3. Call Narrator to write the next episode.
    4. Post episode text to channel (or dry_run_chat_id if preview).
    5. Post interactive poll to channel.
    6. Notify admins.
    """
    target_chat = dry_run_chat_id or settings.CHANNEL_ID
    if not target_chat:
        logger.error("CHANNEL_ID is not configured and no dry_run_chat_id provided.")
        return None

    logger.info(f"Starting episode publishing cycle (Target: {target_chat})...")

    # Step 1: Close active poll if this is a live release (not a preview)
    last_poll_result = None
    if not dry_run_chat_id:
        last_poll_result = await PollService.close_active_poll(bot)

    # Step 2: Grab pending viewer suggestions (up to 5 for autonomous triage)
    pending_suggestions = await Repository.get_pending_suggestions(limit=5)

    # Step 3: Generate next episode via CodeCraft API
    narrator = NarratorService()
    try:
        episode_data = await narrator.generate_next_episode(
            last_poll_result=last_poll_result,
            suggestions_to_use=pending_suggestions
        )
    except Exception as e:
        logger.error(f"Error during episode generation: {e}", exc_info=True)
        # Notify admins about error
        for admin_id in settings.ADMIN_IDS:
            try:
                await bot.send_message(admin_id, f"⚠️ Ошибка генерации серии сериала:\n{e}")
            except Exception:
                pass
        return None

    ep_id = episode_data["episode_id"]
    season = episode_data["season"]
    ep_num = episode_data["episode_number"]
    title = episode_data["title"]
    screenplay = episode_data["screenplay"]
    cliffhanger = episode_data.get("cliffhanger", "")
    is_finale = bool(
        episode_data.get("is_finale")
        or not episode_data.get("poll_options")
        or not episode_data.get("poll_question")
    )
    poll_q = episode_data.get("poll_question")
    poll_opts = episode_data.get("poll_options", [])

    # Format the Telegram post
    if is_finale:
        post_parts = [
            f"🎬 СЕЗОН {season}, СЕРИЯ {ep_num}: «{title}» (ФИНАЛ СЕЗОНА)\n",
            screenplay.strip(),
            "\n🏁 ФИНАЛ 1-ГО СЕЗОНА! Спасибо всем зрителям, кто голосовал в опросах, предлагал идеи в комментариях и создавал эту историю вместе с нами!"
        ]
    else:
        post_parts = [
            f"🎬 СЕЗОН {season}, СЕРИЯ {ep_num}: «{title}»\n",
            screenplay.strip(),
            "\n👇 Голосуйте в опросе ниже — ваш выбор определит сюжет следующей серии!"
        ]

    full_post_text = "\n".join(post_parts)

    # Step 4: Publish text post
    try:
        msg_id = await send_chunked_message(bot, target_chat, full_post_text)
        await Repository.update_episode_meta(episode_id=ep_id, telegram_message_id=msg_id)
        logger.info(f"Episode {ep_num} text published successfully (Message ID: {msg_id})")
    except Exception as e:
        logger.error(f"Failed to send episode message to {target_chat}: {e}", exc_info=True)
        return None

    # Step 5: Publish poll (skipped for finale or episodes without poll)
    if not is_finale and poll_q and len(poll_opts) >= 2:
        try:
            poll_res = await PollService.create_and_send_poll(
                bot=bot,
                channel_id=target_chat,
                episode_id=ep_id,
                question=poll_q,
                options=poll_opts
            )
            if poll_res:
                logger.info(f"Poll for episode {ep_num} sent successfully.")
        except Exception as e:
            logger.error(f"Failed to send poll for episode {ep_num}: {e}", exc_info=True)
    else:
        logger.info(f"Skipping poll publication for episode {ep_num} (Finale episode, no poll).")

    # Step 6: Notify admins if live release
    if not dry_run_chat_id:
        for admin_id in settings.ADMIN_IDS:
            try:
                if is_finale:
                    admin_msg = (
                        f"🏁 Финальная серия {ep_num} «{title}» успешно опубликована в канале!\n"
                        f"Опрос не создавался (финал 1-го сезона)."
                    )
                else:
                    admin_msg = (
                        f"✅ Серия {ep_num} «{title}» успешно опубликована в канале!\n"
                        f"Опрос: «{poll_q}»\n"
                        f"Варианты: {', '.join(poll_opts)}"
                    )
                await bot.send_message(admin_id, admin_msg)
            except Exception:
                pass

    return ep_id
