# bot.py
import os
import tempfile
import logging
import asyncio
from typing import List, Tuple, Dict, Any, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, FSInputFile, Document as TgDocument, CallbackQuery
)
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.session.aiohttp import AiohttpSession
from aiohttp import ClientTimeout
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv

from parsers.egrn_parser import parse_egrn_xml, EGRNData, Coord
from generator.docx_builder import build_section1_docx
from parsers.kpt_parser import parse_kpt_xml, Zone
from utils.spatial import Parcel, ZoneShape, determine_zone

# ---------------------------------- ЛОГИ ---------------------------------- #
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("gpzu-bot")

# --------------------------------- НАСТРОЙКИ -------------------------------- #
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
if not BOT_TOKEN:
    raise RuntimeError("Put TELEGRAM_BOT_TOKEN=... into .env")

# ---------- Справочник зон ----------
ZONES: Dict[str, str] = {
    "Ж-1":"Зона многоэтажной, среднеэтажной, малоэтажной многоквартирной жилой застройки",
    "Ж-2":"Зона застройки индивидуальными жилыми домами и домами блокированной застройки",
    "Ж-Р":"Зона застройки многоквартирными домами, в границах которой предусматривается осуществление деятельности по комплексному развитию территории",
    "ОЖ":"Общественно-жилая зона",
    "ОД-1":"Зона общественно-делового и коммерческого назначения",
    "ОД-2":"Зона обслуживания объектов, необходимых для осуществления производственной и предпринимательской деятельности",
    "ОД-3":"Зона объектов образования",
    "ОД-4":"Зона объектов здравоохранения",
    "ОД-5":"Зона объектов спорта",
    "П-1":"Производственная зона",
    "П-2":"Коммунально-складская зона",
    "И":"Зона инженерной инфраструктуры",
    "Т-1":"Зона объектов улично-дорожной сети",
    "Т-2":"Зона объектов автомобильного транспорта",
    "Т-3":"Зона объектов железнодорожного транспорта",
    "Т-4":"Зона объектов воздушного транспорта",
    "СХ-1":"Зона сельскохозяйственного использования",
    "СХ-2":"Зона, предназначенная для ведения садоводства и огородничества",
    "РТ":"Зона режимных территорий",
    "СН-1":"Зона кладбищ и крематориев",
    "СН-2":"Зона складирования и захоронения отходов",
    "ЗН-1":"Зона озелененных территорий общего пользования",
    "ЗН-2":"Зона городских лесов",
    "ЗН-3":"Зона озелененных территорий специального назначения",
    "ЗН-4":"Зона природного ландшафта",
    "Р-1":"Зона водного спорта и отдыха",
    "Р-2":"Зона отдыха и туризма",
}

# --------------------------------- СОСТОЯНИЯ -------------------------------- #
class States(StatesGroup):
    WAIT_EGRN = State()
    WAIT_KPT  = State()
    WAIT_DECISION = State()
    WAIT_ZONE_SELECT = State()

router = Router()

# ----------------------- FSM сериализация простых типов ---------------------- #
from parsers.egrn_parser import Coord as ECoord  # алиас на случай конфликта имён

def _coords_to_list(coords: List[ECoord]) -> List[Dict[str, Any]]:
    return [{"num": c.num, "x": c.x, "y": c.y} for c in coords or []]

def _coords_from_list(items: List[Dict[str, Any]]) -> List[ECoord]:
    return [ECoord(num=i.get("num"), x=i.get("x", ""), y=i.get("y", "")) for i in (items or [])]

def _egrn_to_dict(e: EGRNData) -> Dict[str, Any]:
    return {
        "cadnum": e.cadnum,
        "address": e.address,
        "area": e.area,
        "region": e.region,
        "municipality": e.municipality,
        "settlement": e.settlement,
        "coordinates": _coords_to_list(e.coordinates),
        "is_land": e.is_land,
        "has_coords": e.has_coords,
        "capital_objects": e.capital_objects or [],
    }

def _egrn_from_dict(d: Dict[str, Any]) -> EGRNData:
    return EGRNData(
        cadnum=d.get("cadnum"),
        address=d.get("address"),
        area=d.get("area"),
        region=d.get("region"),
        municipality=d.get("municipality"),
        settlement=d.get("settlement"),
        coordinates=_coords_from_list(d.get("coordinates", [])),
        is_land=bool(d.get("is_land")),
        has_coords=bool(d.get("has_coords")),
        capital_objects=list(d.get("capital_objects", [])),
    )

# ---------------------------- ВСПОМОГАТЕЛЬНЫЕ ---------------------------- #
async def _download_with_retries(bot: Bot, file_path: str, *, retries: int = 3) -> bytes:
    last_exc: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            stream = await bot.download_file(file_path)
            data = stream.read()
            return data
        except Exception as ex:
            last_exc = ex
            wait = min(2 ** attempt, 10)
            logger.warning("download retry %d/%d after error: %s; sleep %ss", attempt, retries, ex, wait)
            await asyncio.sleep(wait)
    raise last_exc or RuntimeError("download failed")

def _format_zone_for_template(code: Optional[str]) -> str:
    if not code:
        return ""
    full = ZONES.get(code, "").strip()
    return f"{code} «{full}»" if full else code

def _try_detect_code(detected_zone_name: Optional[str]) -> Optional[str]:
    if not detected_zone_name:
        return None
    s = detected_zone_name.upper()
    for code in ZONES.keys():
        if code in s or f"({code})" in s:
            return code
    return None

def _decision_keyboard_two() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Подготовить ГПЗУ", callback_data="act_prepare")
    kb.button(text="Выбрать зону вручную", callback_data="act_manual")
    kb.adjust(1)
    return kb

def _manual_only_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Выбрать зону вручную", callback_data="act_manual")
    kb.adjust(1)
    return kb

def _start_over_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Подготовить новый градплан", callback_data="act_restart")
    kb.adjust(1)
    return kb

def _zones_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    for code in ZONES.keys():
        kb.button(text=code, callback_data=f"zone:{code}")
    kb.adjust(3)
    return kb

async def _generate_and_send_gpzu(m: Message, egrn: EGRNData, zone_code: Optional[str]):
    zone_name_for_tpl = _format_zone_for_template(zone_code)
    tmp_path = None
    try:
        docx_bytes = build_section1_docx(egrn, zone_name=zone_name_for_tpl, zone_code=zone_code)
        fn = f"GPZU_Razdel1_{egrn.cadnum or 'no-cad'}.docx"
        with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
            tmp.write(docx_bytes)
            tmp_path = tmp.name
        await m.answer_document(FSInputFile(tmp_path, filename=fn), caption="ГПЗУ — раздел 1.")
        logger.info("DOCX сформирован и отправлен: %s", fn)
        await m.answer("Готово.", reply_markup=_start_over_keyboard().as_markup())
    except Exception as ex:
        logger.exception("Ошибка генерации/отправки DOCX: %s", ex)
        await m.answer(f"Не удалось сформировать документ: {ex}", reply_markup=_start_over_keyboard().as_markup())
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except Exception:
                pass

# --------------------------------- ХЕНДЛЕРЫ --------------------------------- #
@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(States.WAIT_EGRN)
    await m.answer(
        "Шаг 1: пришлите XML/ZIP выписки ЕГРН по земельному участку (файлом).\n"
        "Шаг 2: затем пришлите XML КПТ для квартала — определю территориальную зону."
    )

@router.callback_query(F.data == "act_restart")
async def cb_restart(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    await state.set_state(States.WAIT_EGRN)
    await call.message.answer("Начнём заново.\nШаг 1: пришлите XML/ZIP выписки ЕГРН по земельному участку (файлом).")

@router.message(States.WAIT_EGRN, F.document)
async def got_egrn(m: Message, state: FSMContext):
    doc: TgDocument = m.document
    if not doc.file_name or not (doc.file_name.lower().endswith(".xml") or doc.file_name.lower().endswith(".zip")):
        await m.answer("Это не XML/ZIP. Пришлите файл выписки ЕГРН.", reply_markup=_start_over_keyboard().as_markup())
        return

    try:
        file = await m.bot.get_file(doc.file_id)
        egrn_bytes = await _download_with_retries(m.bot, file.file_path)
        logger.info("Получен файл ЕГРН: %s (%d байт)", doc.file_name, len(egrn_bytes))
    except Exception as ex:
        logger.exception("Ошибка скачивания ЕГРН: %s", ex)
        await m.answer(f"Не удалось скачать файл: {ex}", reply_markup=_start_over_keyboard().as_markup())
        return

    # Парсинг (поддерживает XML и ZIP с XML внутри)
    try:
        egrn: EGRNData = parse_egrn_xml(egrn_bytes)
        sample = ", ".join([f"{c.num}({c.x};{c.y})" for c in (egrn.coordinates or [])[:5]])
        logger.info(
            "Распарсили ЕГРН: cadnum=%s, area=%s, is_land=%s, coords=%s; sample: %s; capital=%s",
            egrn.cadnum, egrn.area, egrn.is_land, len(egrn.coordinates or []),
            sample, (egrn.capital_objects or [])
        )
    except Exception as ex:
        logger.exception("Ошибка парсинга ЕГРН: %s", ex)
        await m.answer(f"Ошибка парсинга выписки: {ex}", reply_markup=_start_over_keyboard().as_markup())
        return

    # Проверки корректности выписки
    if not egrn.is_land:
        await m.answer("Это не выписка ЕГРН по земельному участку.", reply_markup=_start_over_keyboard().as_markup())
        return
    if not egrn.cadnum:
        await m.answer("Не удалось определить кадастровый номер в выписке.", reply_markup=_start_over_keyboard().as_markup())
        return
    if not egrn.has_coords:
        await m.answer("В выписке нет координат границ участка.", reply_markup=_start_over_keyboard().as_markup())
        return

    # Сохраняем ЕГРН и просим КПТ + показываем КН
    await state.update_data(egrn=_egrn_to_dict(egrn))
    await state.set_state(States.WAIT_KPT)
    await m.answer(
        f"Выписка корректна.\nКадастровый номер: *{egrn.cadnum}*\n\n"
        f"Шаг 2: пришлите XML КПТ (карта-план территории).",
        parse_mode="Markdown"
    )

@router.message(States.WAIT_KPT, F.document)
async def got_kpt(m: Message, state: FSMContext):
    data = await state.get_data()
    if "egrn" not in data:
        await m.answer("Сначала пришлите ЕГРН. /start", reply_markup=_start_over_keyboard().as_markup())
        return
    egrn = _egrn_from_dict(data["egrn"])

    doc: TgDocument = m.document
    if not doc.file_name or not doc.file_name.lower().endswith(".xml"):
        await m.answer("Это не XML. Пришлите XML файл КПТ.", reply_markup=_start_over_keyboard().as_markup())
        return

    try:
        file = await m.bot.get_file(doc.file_id)
        kpt_bytes = await _download_with_retries(m.bot, file.file_path)
        logger.info("Получен XML КПТ: %s (%d байт)", doc.file_name, len(kpt_bytes))
    except Exception as ex:
        logger.exception("Ошибка скачивания КПТ: %s", ex)
        await m.answer(f"Не удалось скачать КПТ: {ex}", reply_markup=_start_over_keyboard().as_markup())
        return

    try:
        zones_raw: List[Zone] = parse_kpt_xml(kpt_bytes)
        logger.info("Найдено зон в КПТ: %d", len(zones_raw))
    except Exception as ex:
        logger.exception("Ошибка парсинга КПТ: %s", ex)
        await m.answer(f"Ошибка парсинга КПТ: {ex}", reply_markup=_start_over_keyboard().as_markup())
        return

    # Контур ЗУ
    parcel_coords: List[Tuple[float, float]] = []
    for c in egrn.coordinates or []:
        try:
            parcel_coords.append((float((c.x or '').replace(',', '.')),
                                  float((c.y or '').replace(',', '.'))))
        except Exception:
            continue

    # Определение зоны
    detected_code: Optional[str] = None
    if zones_raw and parcel_coords:
        try:
            parcel = Parcel(contour=parcel_coords)
            zones = [ZoneShape(name=z.name, contours=z.contours) for z in zones_raw]
            detected_name = determine_zone(parcel, zones)
            detected_code = _try_detect_code(detected_name)
        except Exception as ex:
            logger.warning("Не удалось определить зону: %s", ex)

    await state.update_data(detected_zone_code=detected_code or "")
    await state.set_state(States.WAIT_DECISION)

    # Если определили — две кнопки; если нет — только ручной выбор
    if detected_code:
        full_line = _format_zone_for_template(detected_code)
        await m.answer(
            f"Определена территориальная зона: *{full_line}*.\nВыберите действие:",
            reply_markup=_decision_keyboard_two().as_markup(),
            parse_mode="Markdown"
        )
    else:
        await m.answer(
            "Не удалось однозначно определить территориальную зону по КПТ.",
            reply_markup=_manual_only_keyboard().as_markup()
        )

# ------ действия после анализа ------
@router.callback_query(States.WAIT_DECISION, F.data == "act_prepare")
async def cb_prepare(call: CallbackQuery, state: FSMContext):
    await call.answer()
    data = await state.get_data()
    egrn = _egrn_from_dict(data["egrn"])
    zone_code = data.get("detected_zone_code") or None
    await _generate_and_send_gpzu(call.message, egrn, zone_code)
    await state.clear()

@router.callback_query(States.WAIT_DECISION, F.data == "act_manual")
async def cb_manual(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(States.WAIT_ZONE_SELECT)
    await call.message.answer(
        "Выберите территориальную зону:",
        reply_markup=_zones_keyboard().as_markup()
    )

# ------ выбор конкретной зоны ------
@router.callback_query(States.WAIT_ZONE_SELECT, F.data.startswith("zone:"))
async def cb_zone_select(call: CallbackQuery, state: FSMContext):
    await call.answer()
    code = call.data.split(":", 1)[1]
    if code not in ZONES:
        await call.message.answer("Неизвестный код зоны, выберите из списка.", reply_markup=_start_over_keyboard().as_markup())
        return
    data = await state.get_data()
    if "egrn" not in data:
        await call.message.answer("Сначала пришлите ЕГРН. /start", reply_markup=_start_over_keyboard().as_markup())
        await state.clear()
        return
    egrn = _egrn_from_dict(data["egrn"])
    await _generate_and_send_gpzu(call.message, egrn, code)
    await state.clear()

@router.message(States.WAIT_DECISION)
async def expecting_decision(m: Message, state: FSMContext):
    await m.answer("Выберите действие через кнопки.", reply_markup=_start_over_keyboard().as_markup())

@router.message(States.WAIT_ZONE_SELECT)
async def expecting_zone(m: Message, state: FSMContext):
    await m.answer("Пожалуйста, выберите зону из кнопок ниже.", reply_markup=_start_over_keyboard().as_markup())

# ----------------------------------- MAIN ----------------------------------- #
async def main():
    session = AiohttpSession(timeout=ClientTimeout(total=300, sock_read=300, connect=30))
    bot = Bot(BOT_TOKEN, session=session)
    bot.session.timeout = 60  # числовой timeout для long-polling

    dp = Dispatcher(storage=MemoryStorage())
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Webhook удалён, включаем long polling")
    except Exception as ex:
        logger.warning("Не удалось удалить webhook: %s", ex)

    dp.include_router(router)
    logger.info("✅ Bot is starting polling...")
    await dp.start_polling(bot, polling_timeout=30)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("⏹ Bot stopped")
