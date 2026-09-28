import logging
from typing import Dict, Any, List, Optional
from aiogram import Bot
from database.repository import Repository

logger = logging.getLogger(__name__)


class PollService:
    @staticmethod
    async def create_and_send_poll(
        bot: Bot,
        channel_id: str | int,
        episode_id: int,
        question: str,
        options: List[str]
    ) -> Optional[Dict[str, Any]]:
        """Send a native Telegram poll to the channel and store it in DB."""
        # Telegram constraint: question <= 300 chars, options 2-10 items <= 100 chars
        clean_question = question[:300]
        clean_options = [opt[:100] for opt in options if opt.strip()][:10]
        if len(clean_options) < 2:
            clean_options = ["Продолжить по плану", "Сменить тактику"]

        try:
            logger.info(f"Sending poll to channel {channel_id}: '{clean_question}' with {len(clean_options)} options")
            poll_message = await bot.send_poll(
                chat_id=channel_id,
                question=clean_question,
                options=clean_options,
                is_anonymous=True,
                allows_multiple_answers=False
            )

            poll = poll_message.poll
            poll_id = poll.id
            message_id = poll_message.message_id

            # Save in database
            db_id = await Repository.add_poll(
                poll_id=poll_id,
                channel_id=str(channel_id),
                message_id=message_id,
                question=clean_question,
                options=clean_options,
                episode_id=episode_id
            )

            # Link poll_id to episode
            await Repository.update_episode_meta(episode_id=episode_id, poll_id=poll_id)

            return {
                "id": db_id,
                "poll_id": poll_id,
                "message_id": message_id,
                "question": clean_question,
                "options": clean_options
            }
        except Exception as e:
            logger.error(f"Failed to send poll to channel {channel_id}: {e}", exc_info=True)
            return None

    @staticmethod
    async def close_active_poll(bot: Bot) -> Optional[Dict[str, Any]]:
        """
        Close the latest active poll in the channel, tally the votes,
        determine the winner option, and mark poll as closed in DB.
        """
        active_poll = await Repository.get_active_poll()
        if not active_poll:
            logger.info("No active poll found in database to close.")
            return None

        poll_id = active_poll["poll_id"]
        channel_id = active_poll["channel_id"]
        message_id = active_poll["message_id"]
        options = active_poll["options"]

        winner_option = options[0] if options else "Продолжить сюжет"
        total_voters = 0

        try:
            logger.info(f"Closing poll {poll_id} (msg {message_id}) in channel {channel_id}...")
            stopped_poll = await bot.stop_poll(chat_id=channel_id, message_id=message_id)

            total_voters = stopped_poll.total_voter_count

            # Find option with maximum votes
            max_votes = -1
            for opt in stopped_poll.options:
                if opt.voter_count > max_votes:
                    max_votes = opt.voter_count
                    winner_option = opt.text

            logger.info(f"Poll {poll_id} closed. Winner: '{winner_option}' with {max_votes}/{total_voters} votes.")
        except Exception as e:
            logger.warning(
                f"Could not stop poll via Telegram API (possibly already closed or deleted): {e}. "
                f"Defaulting winner to first option '{winner_option}'."
            )

        # Update in database
        await Repository.close_poll(poll_id=poll_id, winner_option=winner_option, total_voters=total_voters)

        return {
            "poll_id": poll_id,
            "question": active_poll["question"],
            "winner_option": winner_option,
            "total_voters": total_voters
        }
