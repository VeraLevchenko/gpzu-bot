# core/utils.py
import asyncio
import logging
from typing import Optional

from aiogram import Bot

logger = logging.getLogger("gpzu-bot.utils")


async def download_with_retries(bot: Bot, file_path: str, *, retries: int = 3) -> bytes:
    """
    Универсальная функция скачивания файла из Telegram с повторами.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            stream = await bot.download_file(file_path)
            data = stream.read()
            return data
        except Exception as ex:
            last_exc = ex
            wait = min(2 ** attempt, 10)
            logger.warning(
                "download retry %d/%d after error: %s; sleep %ss",
                attempt,
                retries,
                ex,
                wait,
            )
            await asyncio.sleep(wait)
    raise last_exc or RuntimeError("download failed")
