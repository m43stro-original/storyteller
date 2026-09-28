import re
import json
import logging
from pathlib import Path
from aiogram import Router, types
from config import settings
from services.comment_agent import CommentAgent
from database.repository import Repository

logger = logging.getLogger(__name__)
comment_router = Router(name="comment_router")
comment_agent = CommentAgent()


def _normalize_chat_id(cid) -> str:
    if cid is None:
        return ""
    s = str(cid).strip()
    # Strip @ if present
    if s.startswith("@"):
        return s.lower()
    # Normalize -100 prefix vs - prefix
    if s.startswith("-100"):
        return s[4:]
    if s.startswith("-"):
        return s[1:]
    return s


@comment_router.message()
async def handle_discussion_comment(message: types.Message):
    """
    Handle comments and messages posted in the discussion group, channel comments, or direct messages.
    """
    # 0. Always log arrival of message immediately!
    sender_desc = (
        f"user={message.from_user.id} ({message.from_user.full_name})"
        if message.from_user else
        f"sender_chat={message.sender_chat.id} ({message.sender_chat.title})"
        if message.sender_chat else "unknown"
    )
    text_preview = (message.text or message.caption or "<no text>")[:80]
    logger.info(
        f"📥 Входящий апдейт [msg_id={message.message_id}] в чате {message.chat.id} ({message.chat.title or message.chat.type}) "
        f"от {sender_desc}: «{text_preview}»"
    )

    # 1. Do not process messages sent by our own bot (prevent infinite loop)
    bot_me = await message.bot.get_me()
    if message.from_user and message.from_user.id == bot_me.id:
        logger.debug("⏩ Пропуск: сообщение от нашего же бота.")
        return

    # 2. Check if message has text
    comment_text = message.text or message.caption
    if not comment_text or not comment_text.strip():
        logger.debug(f"⏩ Пропуск сообщения {message.message_id}: нет текста.")
        return

    # 3. Check chat destination
    # Accept:
    # - Discussion group (settings.DISCUSSION_CHAT_ID)
    # - Channel itself (settings.CHANNEL_ID)
    # - Private messages (chat.type == "private")
    chat_norm = _normalize_chat_id(message.chat.id)
    chat_user = (message.chat.username or "").lower()
    disc_norm = _normalize_chat_id(settings.DISCUSSION_CHAT_ID)
    chan_norm = _normalize_chat_id(settings.CHANNEL_ID)

    is_discussion = bool(disc_norm and (chat_norm == disc_norm or chat_user == disc_norm))
    is_channel = bool(chan_norm and (chat_norm == chan_norm or chat_user == chan_norm))
    is_private = message.chat.type == "private"

    if settings.DISCUSSION_CHAT_ID and not (is_discussion or is_channel or is_private):
        logger.info(
            f"⏩ Пропуск сообщения {message.message_id}: чат {message.chat.id} (norm={chat_norm}) "
            f"не совпадает с DISCUSSION_CHAT_ID {settings.DISCUSSION_CHAT_ID} (norm={disc_norm}) и не ЛС"
        )
        return

    # 4. If the message is the automatic channel post itself forwarded to discussion group, skip
    # (We only want to reply to user comments, not to the root episode post)
    if message.sender_chat and chan_norm and _normalize_chat_id(message.sender_chat.id) == chan_norm and not message.reply_to_message:
        logger.debug("⏩ Пропуск: это корневой пост серии, пересланный каналом в группу.")
        return

    # Check if bot is mentioned explicitly
    bot_mentioned = bool(bot_me.username and f"@{bot_me.username.lower()}" in comment_text.lower())

    # 4.1 Filter out user-to-user dialogues and disputes in group chats
    if message.reply_to_message and not is_private:
        is_reply_to_our_bot = bool(
            message.reply_to_message.from_user
            and message.reply_to_message.from_user.id == bot_me.id
        )
        is_reply_to_channel_post = bool(
            (message.reply_to_message.sender_chat and chan_norm and _normalize_chat_id(message.reply_to_message.sender_chat.id) == chan_norm)
            or getattr(message.reply_to_message, "is_automatic_forward", False)
        )

        # If replying to another user (not the bot and not the channel post), and our bot is NOT mentioned:
        if not is_reply_to_our_bot and not is_reply_to_channel_post and not bot_mentioned:
            logger.info(
                f"⏩ Пропуск сообщения {message.message_id}: общение/спор между пользователями "
                f"(ответ сообщению {message.reply_to_message.message_id}), бот не упомянут."
            )
            return

    # 5. Resolve author name and username
    author_name = "Житель Кудрово"
    author_username = None
    if message.sender_chat:
        author_name = message.sender_chat.title or "Канал"
        author_username = message.sender_chat.username
    elif message.from_user:
        # Note: Channel_Bot (id=136817688) or GroupAnonymousBot represents human admin
        if message.from_user.id in (136817688, 1087968824):
            author_name = message.author_signature or (message.sender_chat.title if message.sender_chat else "Администратор")
            author_username = message.sender_chat.username if message.sender_chat else None
        else:
            author_name = message.from_user.full_name or message.from_user.first_name or "Житель"
            author_username = message.from_user.username

    # 6. Clean bot username mention if present
    clean_text = comment_text
    if bot_me.username:
        clean_text = re.sub(rf"@{bot_me.username}\b", "", clean_text, flags=re.IGNORECASE).strip()
    if not clean_text:
        clean_text = comment_text

    # 7. Context gathering: character lore, current story state, and episode/message being discussed
    state = await Repository.get_story_state(season=1)

    # Base lore descriptions from lore_bible.json:
    lore_chars_list = []
    try:
        lore_file = Path(settings.LORE_BIBLE_PATH)
        if lore_file.exists():
            with open(lore_file, "r", encoding="utf-8") as f:
                lore_json = json.load(f)
            for c in lore_json.get("core_characters", []):
                role = f" ({c.get('role')})" if c.get("role") else ""
                traits = f": {c.get('traits')}" if c.get("traits") else ""
                lore_chars_list.append(f"• {c.get('name')}{role}{traits}")
    except Exception as e:
        logger.warning(f"Could not load lore characters for comment context: {e}")

    # Dynamic status of characters from database:
    statuses_list = []
    for name, st in (state.get("character_statuses") or {}).items():
        statuses_list.append(f"• {name}: {st}")

    characters_lore = "\n".join(lore_chars_list) if lore_chars_list else None
    characters_status = "\n".join(statuses_list) if statuses_list else None
    story_synopsis = state.get("rolling_synopsis") or None

    episode_context = None
    if message.reply_to_message and (message.reply_to_message.text or message.reply_to_message.caption):
        reply_content = message.reply_to_message.text or message.reply_to_message.caption
        episode_context = f"Сообщение/пост, на который отвечает пользователь:\n{reply_content[:600]}..."
    else:
        latest_ep = await Repository.get_latest_episode(season=1)
        if latest_ep:
            episode_context = (
                f"Серия {latest_ep['episode_number']}: «{latest_ep['title']}»\n"
                f"Клиффхэнгер: {latest_ep.get('cliffhanger', '')}\n"
                f"Содержание:\n{latest_ep.get('content', '')[:500]}..."
            )

    logger.info(f"🤖 Запуск анализа комментария через Groq ({settings.GROQ_MODEL}) от {author_name}: «{clean_text[:60]}»...")

    # 8. Delegate to Groq comment agent
    reply_text = await comment_agent.process_comment(
        chat_id=str(message.chat.id),
        message_id=message.message_id,
        author_name=author_name,
        author_username=author_username,
        comment_text=clean_text,
        episode_context=episode_context,
        characters_lore=characters_lore,
        characters_status=characters_status,
        story_synopsis=story_synopsis
    )

    if reply_text:
        try:
            logger.info(f"💬 Отправка ответа в чат {message.chat.id}: «{reply_text}»")
            await message.reply(text=reply_text, parse_mode=None)
        except Exception as e:
            logger.warning(f"⚠️ message.reply не сработал ({e}), отправляю через direct send_message...")
            try:
                await message.bot.send_message(
                    chat_id=message.chat.id,
                    text=reply_text,
                    message_thread_id=message.message_thread_id,
                    parse_mode=None
                )
                logger.info(f"💬 Успешно отправлено через direct send_message в чат {message.chat.id}")
            except Exception as e2:
                logger.error(f"❌ Ошибка отправки ответа на комментарий {message.message_id}: {e2}", exc_info=True)
    else:
        logger.info(f"ℹ️ Комментарий {message.message_id} обработан, ответ не требуется (или идея сохранена).")
