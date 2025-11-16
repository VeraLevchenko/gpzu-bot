# bot.py
import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.fsm.storage.memory import MemoryStorage
from aiohttp import ClientTimeout

from core.config import BOT_TOKEN
from flows.menu import menu_router
from flows.kaiten_flow import kaiten_router
from flows.midmif_flow import midmif_router
from flows.tu_flow import tu_router
from flows.gp_flow import gp_router
from flows.checklist_flow import checklist_router
from flows.gpzu_flow import gpzu_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("gpzu-bot")


async def main():
    session = AiohttpSession(timeout=ClientTimeout(total=300, sock_read=300, connect=30))
    bot = Bot(BOT_TOKEN, session=session)
    bot.session.timeout = 60

    dp = Dispatcher(storage=MemoryStorage())

    # Подключаем все роутеры
    dp.include_router(menu_router)
    dp.include_router(kaiten_router)
    dp.include_router(midmif_router)
    dp.include_router(tu_router)
    dp.include_router(gp_router)
    dp.include_router(checklist_router)
    dp.include_router(gpzu_router)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook удалён, включаем long polling")
    except Exception as ex:
        logger.warning("Не удалось удалить webhook: %s", ex)

    logger.info("✅ Bot is starting polling...")
    await dp.start_polling(bot, polling_timeout=30)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("⏹ Bot stopped")
