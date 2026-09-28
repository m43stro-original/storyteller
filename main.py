import asyncio
import logging
import sys
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties

from config import settings
from database.db import init_db
from handlers.admin_handlers import admin_router
from handlers.comment_handlers import comment_router
from scheduler.episode_job import run_episode_cycle

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("kudrovo_bot")


async def setup_scheduler(bot: Bot) -> AsyncIOScheduler:
    scheduler = AsyncIOScheduler(timezone=settings.TIMEZONE)

    if settings.SCHEDULE_INTERVAL_HOURS:
        trigger = IntervalTrigger(hours=settings.SCHEDULE_INTERVAL_HOURS)
        logger.info(f"Scheduling episode releases every {settings.SCHEDULE_INTERVAL_HOURS} hours.")
    else:
        # Support multiple CronTriggers separated by ';' or newline (e.g. '10 12 * * *; 0 19 * * *')
        import re
        cron_list = [c.strip() for c in re.split(r"[;\n]", settings.SCHEDULE_CRON) if c.strip()]
        if not cron_list:
            cron_list = ["0 19 * * *"]

        for idx, cron_expr in enumerate(cron_list, start=1):
            trigger = CronTrigger.from_crontab(cron_expr, timezone=settings.TIMEZONE)
            job_id = f"publish_episode_job_{idx}" if len(cron_list) > 1 else "publish_episode_job"
            scheduler.add_job(
                run_episode_cycle,
                trigger=trigger,
                args=[bot],
                id=job_id,
                replace_existing=True,
                misfire_grace_time=3600
            )
            logger.info(f"Scheduling episode releases with cron: '{cron_expr}' ({settings.TIMEZONE}) [Job ID: {job_id}].")

    scheduler.start()
    return scheduler


async def main():
    logger.info("Starting Kudrovo Series Telegram Bot...")

    # 1. Initialize SQLite Database
    await init_db()

    # 2. Check essential configurations
    if not settings.TELEGRAM_BOT_TOKEN:
        logger.warning(
            "TELEGRAM_BOT_TOKEN is not configured! Please provide your bot token in .env file."
        )

    # 3. Setup Bot and Dispatcher
    bot = Bot(
        token=settings.TELEGRAM_BOT_TOKEN or "123456:dummy-token-for-initialization",
        default=DefaultBotProperties(parse_mode=None)
    )
    dp = Dispatcher()

    # Register routers (order matters: admin commands first, general comments second)
    dp.include_router(admin_router)
    dp.include_router(comment_router)

    # 4. Setup APScheduler
    scheduler = await setup_scheduler(bot)

    logger.info("Bot startup completed. Listening for updates...")

    try:
        await dp.start_polling(bot, allowed_updates=["message", "poll", "poll_answer", "channel_post"])
    finally:
        logger.info("Shutting down bot and scheduler...")
        scheduler.shutdown(wait=False)
        await bot.session.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped.")
