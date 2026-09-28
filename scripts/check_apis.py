#!/usr/bin/env python3
"""
Diagnostic utility script to test API connections and Telegram credentials.
Usage:
    python scripts/check_apis.py
"""

import asyncio
import sys
from pathlib import Path

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from openai import AsyncOpenAI
from aiogram import Bot
from config import settings


async def check_codecraft():
    print("\n[1/3] Проверка CodeCraft API (DeepSeek)...")
    if not settings.CODECRAFT_API_KEY:
        print("  ⚠️ CODECRAFT_API_KEY не задан в .env файле.")
        return False

    client = AsyncOpenAI(
        base_url=settings.CODECRAFT_BASE_URL,
        api_key=settings.CODECRAFT_API_KEY
    )
    try:
        res = await client.chat.completions.create(
            model=settings.CODECRAFT_MODEL,
            messages=[{"role": "user", "content": "Скажи одно слово: Кудрово."}],
            max_tokens=10
        )
        ans = res.choices[0].message.content
        print(f"  ✅ CodeCraft API ({settings.CODECRAFT_MODEL}) доступен! Ответ: {ans.strip()}")
        return True
    except Exception as e:
        print(f"  ❌ Ошибка подключения к CodeCraft API: {e}")
        return False


async def check_groq():
    print("\n[2/3] Проверка Groq API (GPT OSS)...")
    if not settings.GROQ_API_KEY:
        print("  ⚠️ GROQ_API_KEY не задан в .env файле.")
        return False

    client = AsyncOpenAI(
        base_url=settings.GROQ_BASE_URL,
        api_key=settings.GROQ_API_KEY
    )
    try:
        res = await client.chat.completions.create(
            model=settings.GROQ_MODEL,
            messages=[{"role": "user", "content": "Привет! Ответь одним словом: Диспетчер."}],
            max_tokens=10
        )
        ans = res.choices[0].message.content
        print(f"  ✅ Groq API ({settings.GROQ_MODEL}) доступен! Ответ: {ans.strip()}")
        return True
    except Exception as e:
        print(f"  ❌ Ошибка подключения к Groq API: {e}")
        return False


async def check_telegram():
    print("\n[3/3] Проверка Telegram Bot Token...")
    if not settings.TELEGRAM_BOT_TOKEN:
        print("  ⚠️ TELEGRAM_BOT_TOKEN не задан в .env файле.")
        return False

    try:
        bot = Bot(token=settings.TELEGRAM_BOT_TOKEN)
        me = await bot.get_me()
        print(f"  ✅ Токен валиден! Бот: @{me.username} ({me.full_name}, ID: {me.id})")
        await bot.session.close()
        return True
    except Exception as e:
        print(f"  ❌ Ошибка проверки токена бота: {e}")
        return False


async def main():
    print("=" * 60)
    print("  Диагностика API для сериала «Хроники Кудрово»")
    print("=" * 60)

    cc_ok = await check_codecraft()
    groq_ok = await check_groq()
    tg_ok = await check_telegram()

    print("\n" + "=" * 60)
    if cc_ok and groq_ok and tg_ok:
        print("🎉 Все сервисы подключены и готовы к работе!")
    else:
        print("💡 Заполните отсутствующие ключи в файле .env")
    print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
