# flows/midmif_flow.py
import os
import tempfile
import logging
from typing import List

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, Document as TgDocument, FSInputFile

from core.utils import download_with_retries
from parsers.egrn_parser import parse_egrn_xml, EGRNData, Coord as ECoord
from generator.midmif_builder import build_mid_mif_from_coords

logger = logging.getLogger("gpzu-bot.midmif")

midmif_router = Router()


# ----------------------------- СОСТОЯНИЯ ----------------------------- #
class MidMifStates(StatesGroup):
    WAIT_EGRN = State()  # ждём файл выписки ЕГРН


# ----------------------------- ХЕНДЛЕРЫ ----------------------------- #
@midmif_router.message(F.text == "2. Подготовить MID/MIF")
async def midmif_entry(m: Message, state: FSMContext):
    """
    Точка входа в сценарий формирования координат для MID/MIF.
    """
    await state.clear()
    await state.set_state(MidMifStates.WAIT_EGRN)
    await m.answer(
        "Подготовка MID/MIF.\n\n"
        "Прикрепите выписку из ЕГРН на земельный участок "
        "в формате *.xml* или *.zip* (файл выписки).\n\n"
        "Я выгружу координаты контура участка, а затем сформирую файлы MIF и MID.",
        parse_mode="Markdown",
    )


@midmif_router.message(MidMifStates.WAIT_EGRN, F.document)
async def midmif_got_egrn(m: Message, state: FSMContext):
    """
    Принимаем выписку, парсим координаты, формируем замкнутый контур,
    выводим таблицу: № точки, X, Y и отдаём файлы MID/MIF.
    """
    doc: TgDocument = m.document

    if not doc.file_name or not (
        doc.file_name.lower().endswith(".xml")
        or doc.file_name.lower().endswith(".zip")
    ):
        await m.answer(
            "Это не XML/ZIP-файл.\n"
            "Пожалуйста, пришлите выписку из ЕГРН в формате *.xml* или *.zip*."
        )
        return

    # Скачиваем и парсим выписку
    try:
        file = await m.bot.get_file(doc.file_id)
        egrn_bytes = await download_with_retries(m.bot, file.file_path)
        logger.info(
            "MID/MIF: получен файл ЕГРН: %s (%d байт)",
            doc.file_name,
            len(egrn_bytes),
        )

        egrn: EGRNData = parse_egrn_xml(egrn_bytes)
    except Exception as ex:
        logger.exception("MID/MIF: ошибка парсинга ЕГРН: %s", ex)
        await m.answer(
            f"Не удалось разобрать выписку ЕГРН: {ex}\n"
            "Проверьте, что приложен корректный XML/ZIP-файл."
        )
        await state.clear()
        return

    # Проверяем, что это именно ЗУ
    if not egrn.is_land:
        await m.answer(
            "Это не выписка ЕГРН по земельному участку.\n"
            "Пожалуйста, прикрепите выписку на земельный участок."
        )
        await state.clear()
        return

    coords: List[ECoord] = egrn.coordinates or []
    if not coords:
        await m.answer("В выписке ЕГРН нет координат границ участка.")
        await state.clear()
        return

    # Нормализуем нумерацию: если num пустой — проставляем 1,2,3...
    normalized: List[ECoord] = []
    for idx, c in enumerate(coords, start=1):
        num = (c.num or "").strip() or str(idx)
        normalized.append(ECoord(num=num, x=c.x, y=c.y))

    # Замыкаем контур: добавляем первую точку в конец, если она ещё не совпадает
    closed = list(normalized)
    first = normalized[0]
    last = normalized[-1]
    if first.x != last.x or first.y != last.y or first.num != last.num:
        closed.append(first)

    # 1) выводим координаты в виде таблицы
    cad = egrn.cadnum or "—"
    total = len(closed)

    lines = []
    lines.append(
        f"Координаты контура земельного участка *{cad}* "
        f"(точек: {total}, включая замыкающую):"
    )
    lines.append("```")
    lines.append(f"{'№':>4} {'X':>20} {'Y':>20}")
    for c in closed:
        lines.append(f"{c.num:>4} {c.x:>20} {c.y:>20}")
    lines.append("```")

    await m.answer("\n".join(lines), parse_mode="Markdown")

    # 2) Формируем MID/MIF
    try:
        coords_for_builder = [(c.num, c.x, c.y) for c in normalized]  # без замыкающей
        base_name, mif_bytes, mid_bytes = build_mid_mif_from_coords(
            egrn.cadnum,
            egrn.area,
            coords_for_builder,
        )
    except Exception as ex:
        logger.exception("MID/MIF: ошибка генерации MID/MIF: %s", ex)
        await m.answer(
            f"Не удалось сформировать файлы MID/MIF: {ex}"
        )
        await state.clear()
        return

    # 3) Отдаём файлы пользователю
    mif_tmp = None
    mid_tmp = None
    try:
        # временные файлы
        mif_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mif")
        mid_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mid")

        mif_tmp.write(mif_bytes)
        mif_tmp.flush()
        mid_tmp.write(mid_bytes)
        mid_tmp.flush()

        mif_tmp_name = mif_tmp.name
        mid_tmp_name = mid_tmp.name

        mif_tmp.close()
        mid_tmp.close()

        mif_name = f"{base_name}.mif"
        mid_name = f"{base_name}.mid"

        await m.answer_document(
            FSInputFile(mif_tmp_name, filename=mif_name),
            caption="Файл MIF с контуром и подписями точек.",
        )
        await m.answer_document(
            FSInputFile(mid_tmp_name, filename=mid_name),
            caption="Файл MID (атрибуты объектов).",
        )

    finally:
        # чистим временные файлы
        for path in (mif_tmp and mif_tmp.name, mid_tmp and mid_tmp.name):
            if path and os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass

    await state.clear()


@midmif_router.message(MidMifStates.WAIT_EGRN)
async def midmif_waiting_egrn_fallback(m: Message, state: FSMContext):
    """
    Если на шаге ожидания выписки пользователь отправляет что-то, но не файл.
    """
    await m.answer(
        "Сейчас я жду файл выписки ЕГРН (XML или ZIP).\n"
        "Пожалуйста, прикрепите файл выписки.",
    )
