import os
import tempfile
import logging
import asyncio

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import Message, FSInputFile, Document as TgDocument
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from dotenv import load_dotenv

from parsers.egrn_parser import parse_egrn_xml, EGRNData
from generator.docx_builder import build_section1_docx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("gpzu-bot")

load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Put TELEGRAM_BOT_TOKEN=... into .env")

class States(StatesGroup):
    WAIT_EGRN = State()

router = Router()

@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.set_state(States.WAIT_EGRN)
    await m.answer(
        "Пришлите XML выписки ЕГРН по земельному участку (файлом).\n"
        "Я сформирую DOCX с разделом 1 ГПЗУ (реквизиты, таблица координат, ОКС)."
    )

@router.message(States.WAIT_EGRN, F.document)
async def got_egrn(m: Message, state: FSMContext):
    doc: TgDocument = m.document
    fname = (doc.file_name or "").lower()
    if not fname.endswith(".xml"):
        await m.answer("Это не XML. Пришлите, пожалуйста, файл XML выписки ЕГРН.")
        return

    # скачиваем файл
    try:
        file = await m.bot.get_file(doc.file_id)
        f = await m.bot.download_file(file.file_path)
        xml_bytes = f.read()
        logger.info("Получен XML ЕГРН: %s (%d байт)", fname, len(xml_bytes))
    except Exception as ex:
        logger.exception("Ошибка скачивания XML: %s", ex)
        await m.answer(f"Не удалось скачать XML: {ex}")
        return

    # парсим
    try:
        egrn: EGRNData = parse_egrn_xml(xml_bytes)
        sample = ", ".join([f"{c.num}({c.x};{c.y})" for c in (egrn.coordinates or [])[:5]])
        logger.info(
            "Распарсили ЕГРН: cadnum=%s, area=%s, is_land=%s, coords=%s; sample: %s; capital=%s",
            egrn.cadnum, egrn.area, egrn.is_land, len(egrn.coordinates or []),
            sample, (egrn.capital_objects or [])
        )
    except Exception as ex:
        logger.exception("Ошибка парсинга XML: %s", ex)
        await m.answer(f"Не удалось распарсить ЕГРН XML: {ex}")
        return

    # проверки
    if not egrn.is_land:
        await m.answer("Это не выписка ЕГРН по земельному участку. Пришлите XML ЕГРН на ЗУ.")
        return

    if not egrn.has_coords:
        await m.answer("В выписке отсутствуют координаты границ участка. Таблицу границ сформировать нельзя.")
        return

    # генерация DOCX
    try:
        docx_bytes = build_section1_docx(egrn)
        fn = f"GPZU_Razdel1_{egrn.cadnum or 'no-cad'}.docx"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
            tmp.write(docx_bytes)
            tmp_path = tmp.name
        await m.answer_document(FSInputFile(tmp_path, filename=fn), caption="ГПЗУ — раздел 1.")
        logger.info("DOCX сформирован и отправлен: %s", fn)
    except Exception as ex:
        logger.exception("Ошибка генерации/отправки DOCX: %s", ex)
        await m.answer(f"Не удалось сформировать документ: {ex}")
    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass

    await state.clear()

@router.message(States.WAIT_EGRN)
async def expecting_xml(m: Message, state: FSMContext):
    await m.answer("Ожидаю XML выписки ЕГРН (пришлите файл).")

async def main():
    bot = Bot(BOT_TOKEN)
    dp = Dispatcher(storage=MemoryStorage())
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook удалён, включаем long polling")
    except Exception as ex:
        logger.warning("Не удалось удалить webhook: %s", ex)
    dp.include_router(router)
    logger.info("✅ Bot is starting polling...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("⏹ Bot stopped")
