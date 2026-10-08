"""Настроить бота в Telegram: вебхук, описание, короткое описание, команды.

То же самое выполняется при старте сервиса, если SETUP_BOT_ON_START=true.
Запуск вручную (переменные окружения как у сервиса):

    python scripts/setup_bot.py           # настроить
    python scripts/setup_bot.py --info    # только показать getWebhookInfo
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bot.setup import build_bot, setup_bot
from app.config import get_settings


async def main(info_only: bool) -> int:
    settings = get_settings()
    if not settings.bot_token:
        print("BOT_TOKEN не задан", file=sys.stderr)
        return 2
    bot = build_bot(settings.bot_token)
    try:
        if not info_only:
            results = await setup_bot(bot, settings)
            for step, ok in results.items():
                print(f"{'ok  ' if ok else 'FAIL'} {step}")
        info = await bot.get_webhook_info()
        print(f"webhook: {info.url or '—'}")
        print(f"allowed_updates: {', '.join(info.allowed_updates or []) or '—'}")
        print(f"pending_update_count: {info.pending_update_count}")
        if info.last_error_message:
            print(f"last_error: {info.last_error_message}")
        return 0
    finally:
        await bot.session.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--info", action="store_true", help="только показать getWebhookInfo")
    sys.exit(asyncio.run(main(parser.parse_args().info)))
