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
from generator.midmif_builder import build_mid_mif_from_contours

logger = logging.getLogger("gpzu-bot.midmif")

midmif_router = Router()


class MidMifStates(StatesGroup):
    WAIT_EGRN = State()


@midmif_router.message(F.text == "2. Подготовить MID/MIF")
async def midmif_entry(m: Message, state: FSMContext):
    """
    Точка входа в сценарий подготовки MID/MIF.
    """
    await state.clear()
    await state.set_state(MidMifStates.WAIT_EGRN)
    await m.answer(
        "Подготовка MID/MIF.\n\n"
        "Прикрепите выписку из ЕГРН на земельный участок "
        "в формате *.xml* или *.zip* (файл выписки).\n\n"
        "Я выгружу координаты контуров участка и сформирую файлы MIF и MID.",
        parse_mode="Markdown",
    )


@midmif_router.message(MidMifStates.WAIT_EGRN, F.document)
async def midmif_got_egrn(m: Message, state: FSMContext):
    """
    Принимаем выписку ЕГРН, парсим контуры, пересчитываем номера точек,
    показываем координаты пользователю и формируем MID/MIF.
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

    # 1. Скачиваем и парсим
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

    # 2. Проверяем тип объекта и наличие контуров
    if not egrn.is_land:
        await m.answer(
            "Это не выписка ЕГРН по земельному участку.\n"
            "Пожалуйста, прикрепите выписку на земельный участок."
        )
        await state.clear()
        return

    if not egrn.contours:
        await m.answer(
            "В выписке ЕГРН нет координат границ участка в блоке <contours_location>."
        )
        await state.clear()
        return

    # 3. Пересчитываем нумерацию:
    #    - внутри одного контура точки с одинаковыми координатами получают один и тот же номер
    #    - между контурами номера идут сквозняком: следующий контур начинается с max+1
    numbered_contours: List[List[ECoord]] = []
    next_global_num = 1

    for contour in egrn.contours:
        coord_to_num = {}  # ключ: (normx, normy) → номер точки
        contour_numbered: List[ECoord] = []

        for pt in contour:
            normx = pt.x.strip().replace(",", ".")
            normy = pt.y.strip().replace(",", ".")
            key = (normx, normy)

            if key in coord_to_num:
                num_val = coord_to_num[key]
            else:
                num_val = next_global_num
                coord_to_num[key] = num_val
                next_global_num += 1

            contour_numbered.append(ECoord(num=str(num_val), x=pt.x, y=pt.y))

        numbered_contours.append(contour_numbered)

    # 4. Выводим пользователю координаты (в том порядке, как в выписке, уже с новыми номерами)
    all_points: List[ECoord] = [pt for cnt in numbered_contours for pt in cnt]
    cad = egrn.cadnum or "—"
    total = len(all_points)

    lines: List[str] = []
    lines.append(
        f"Координаты контуров земельного участка *{cad}* "
        f"(точек: {total}, включая замыкающие):"
    )
    lines.append("```")
    lines.append(f"{'№':>4} {'X':>20} {'Y':>20}")
    for c in all_points:
        lines.append(f"{c.num:>4} {c.x:>20} {c.y:>20}")
    lines.append("```")

    await m.answer("\n".join(lines), parse_mode="Markdown")

    # 5. Формируем структуру для билдера: список контуров [[(num, x, y), ...], ...]
    contours_for_builder: List[List[tuple[str, str, str]]] = []
    for cnt in numbered_contours:
        contours_for_builder.append([(c.num, c.x, c.y) for c in cnt])

    # 6. Генерируем MID/MIF
    try:
        base_name, mif_bytes, mid_bytes = build_mid_mif_from_contours(
            egrn.cadnum,
            contours_for_builder,
        )
    except Exception as ex:
        logger.exception("MID/MIF: ошибка генерации MID/MIF: %s", ex)
        await m.answer(f"Не удалось сформировать файлы MID/MIF: {ex}")
        await state.clear()
        return

    # 7. Отдаём файлы пользователю
    mif_tmp = None
    mid_tmp = None
    try:
        mif_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mif")
        mid_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".mid")

        mif_tmp.write(mif_bytes)
        mif_tmp.flush()
        mid_tmp.write(mid_bytes)
        mid_tmp.flush()

        mif_path = mif_tmp.name
        mid_path = mid_tmp.name

        mif_tmp.close()
        mid_tmp.close()

        mif_name = f"{base_name}.mif"
        mid_name = f"{base_name}.mid"

        await m.answer_document(
            FSInputFile(mif_path, filename=mif_name),
            caption="Файл MIF с контурами и точечными объектами.",
        )
        await m.answer_document(
            FSInputFile(mid_path, filename=mid_name),
            caption="Файл MID с семантикой точек (номер точки).",
        )
    finally:
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
    Сообщение по умолчанию, если в состоянии ожидания выписки приходит что-то ещё.
    """
    await m.answer(
        "Сейчас я жду файл выписки ЕГРН (XML или ZIP).\n"
        "Пожалуйста, прикрепите файл выписки.",
    )
