# flows/tu_flow.py
import os
import tempfile
import logging
from typing import Optional

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import (
    Message,
    CallbackQuery,
    Document as TgDocument,
    FSInputFile,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.keyboards import main_menu_kb
from core.utils import download_with_retries
from parsers.egrn_parser import parse_egrn_xml, EGRNData
from generator.tu_requests_builder import (
    build_tu_docs,
    build_tu_docs_with_outgoing,
)

logger = logging.getLogger("gpzu-bot.tu")

tu_router = Router()


# ----------------------------- СОСТОЯНИЯ ----------------------------- #
class TUStates(StatesGroup):
    WAIT_INCOMING = State()  # ждём строку "124245 от 01.11.2025"
    WAIT_EGRN = State()      # ждём файл выписки
    WAIT_ACTION = State()    # ждём выбор способа подготовки ТУ


# --------------------------- КЛАВИАТУРЫ --------------------------- #
def _next_button() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Далее", callback_data="tu:next_to_egrn")
    kb.adjust(1)
    return kb


def _tu_mode_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(
        text="Подготовить ТУ с исходящим номером и датой",
        callback_data="tu:with_outgoing",
    )
    kb.button(
        text="Подготовить ТУ для последующей самостоятельной регистрации",
        callback_data="tu:without_outgoing",
    )
    kb.adjust(1)
    return kb


# --------------------------- ХЕНДЛЕРЫ --------------------------- #
@tu_router.message(F.text == "3. Подготовить запросы ТУ")
async def tu_entry(m: Message, state: FSMContext):
    """
    Точка входа в сценарий "Подготовить запросы ТУ".
    Шаг 1 — просим входящий номер и дату.
    """
    await state.clear()
    await state.set_state(TUStates.WAIT_INCOMING)
    await m.answer(
        "Подготовка запросов ТУ.\n\n"
        "Шаг 1. Введите входящий номер заявления и входящую дату в формате:\n"
        "`124245 от 01.11.2025`\n\n"
        "После ввода вы сможете нажать кнопку «Далее».",
        parse_mode="Markdown",
    )


@tu_router.message(TUStates.WAIT_INCOMING)
async def tu_got_incoming(m: Message, state: FSMContext):
    """
    Принимаем строку вида "124245 от 01.11.2025",
    сохраняем её и предлагаем нажать «Далее».
    """
    text: Optional[str] = (m.text or "").strip()
    if not text:
        await m.answer(
            "Не удалось распознать текст.\n"
            "Пожалуйста, введите номер и дату в формате `124245 от 01.11.2025`.",
            parse_mode="Markdown",
        )
        return

    await state.update_data(incoming=text)
    await m.answer(
        f"Приняла:\n`{text}`\n\n"
        f"Если всё верно, нажмите «Далее».",
        parse_mode="Markdown",
        reply_markup=_next_button().as_markup(),
    )


@tu_router.callback_query(TUStates.WAIT_INCOMING, F.data == "tu:next_to_egrn")
async def tu_next_to_egrn(call: CallbackQuery, state: FSMContext):
    """
    Переходим к шагу 2 — просим прикрепить выписку ЕГРН.
    Кнопок пока нет, просто ждём файл.
    """
    await call.answer()

    data = await state.get_data()
    incoming = data.get("incoming") or "не заполнено"
    logger.info("TU: входящие данные: %s", incoming)

    await state.set_state(TUStates.WAIT_EGRN)
    await call.message.answer(
        "Шаг 2. Прикрепите выписку из ЕГРН на земельный участок "
        "в формате *.xml* или *.zip* (файл выписки).\n\n"
        "После загрузки файла я покажу данные по участку и предложу варианты подготовки ТУ.",
        parse_mode="Markdown",
    )


@tu_router.message(TUStates.WAIT_EGRN, F.document)
async def tu_got_egrn(m: Message, state: FSMContext):
    """
    Принимаем файл выписки (xml или zip), скачиваем, парсим,
    проверяем, что это ЗУ, показываем данные и предлагаем выбрать режим подготовки ТУ.
    """
    doc: TgDocument = m.document

    if not doc.file_name or not (
        doc.file_name.lower().endswith(".xml") or doc.file_name.lower().endswith(".zip")
    ):
        await m.answer(
            "Это не XML/ZIP-файл.\n"
            "Пожалуйста, пришлите выписку из ЕГРН в формате *.xml* или *.zip*."
        )
        return

    data = await state.get_data()
    incoming = data.get("incoming") or "не заполнено"

    await state.update_data(egrn_file_id=doc.file_id, egrn_filename=doc.file_name)

    # Скачиваем и парсим выписку
    try:
        file = await m.bot.get_file(doc.file_id)
        egrn_bytes = await download_with_retries(m.bot, file.file_path)
        logger.info(
            "TU: получен файл ЕГРН: %s (%d байт), входящий=%s",
            doc.file_name,
            len(egrn_bytes),
            incoming,
        )

        egrn: EGRNData = parse_egrn_xml(egrn_bytes)
    except Exception as ex:
        logger.exception("TU: ошибка парсинга ЕГРН: %s", ex)
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

    cadnum = egrn.cadnum or "—"
    area = egrn.area or "—"
    vri = egrn.permitted_use or "—"
    address = egrn.address or "—"

    # Сохраняем минимально необходимые поля для последующей генерации документов
    await state.update_data(
        egrn_cadnum=egrn.cadnum or "",
        egrn_area=egrn.area or "",
        egrn_vri=egrn.permitted_use or "",
        egrn_address=egrn.address or "",
    )

    summary_lines = [
        "Данные по земельному участку из ЕГРН:",
        f"*Кадастровый номер:* `{cadnum}`",
        f"*Площадь:* {area} кв. м",
        f"*ВРИ:* {vri}",
        f"*Адрес:* {address}",
        "",
        "Выберите режим подготовки ТУ:",
    ]
    await m.answer(
        "\n".join(summary_lines),
        parse_mode="Markdown",
        reply_markup=_tu_mode_keyboard().as_markup(),
    )

    await state.set_state(TUStates.WAIT_ACTION)


def _egrn_from_state(data: dict) -> EGRNData:
    """Восстанавливаем EGRNData из данных FSM (минимальный набор полей)."""
    return EGRNData(
        cadnum=data.get("egrn_cadnum") or None,
        address=data.get("egrn_address") or None,
        area=data.get("egrn_area") or None,
        permitted_use=data.get("egrn_vri") or None,
    )


async def _send_tu_docs(
    message: Message,
    docs_data,
):
    """Вспомогательная функция отправки сформированных DOCX-файлов."""
    for doc_name, content in docs_data:
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
                tmp.write(content)
                tmp_path = tmp.name

            await message.answer_document(
                FSInputFile(tmp_path, filename=doc_name)
            )
            logger.info("TU: отправлен файл %s", doc_name)

        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except Exception:
                    pass


@tu_router.callback_query(TUStates.WAIT_ACTION, F.data == "tu:without_outgoing")
async def tu_without_outgoing(call: CallbackQuery, state: FSMContext):
    """
    Вариант: подготовить ТУ для последующей самостоятельной регистрации.
    Исходящий номер и дата не заполняются (поля остаются пустыми).
    """
    await call.answer()

    data = await state.get_data()
    incoming = data.get("incoming") or ""
    egrn = _egrn_from_state(data)

    await call.message.answer(
        "Готовлю ТУ для последующей самостоятельной регистрации..."
    )

    try:
        docs = build_tu_docs(egrn, incoming, out_num=None, out_date=None)

        if not docs:
            await call.message.answer(
                "Не удалось найти шаблоны запросов ТУ.\n"
                "Проверьте, что файлы-шаблоны лежат в папке `templates/tu`.",
                reply_markup=main_menu_kb(),
            )
            await state.clear()
            return

        await _send_tu_docs(call.message, docs)

        await call.message.answer(
            "Запросы ТУ сформированы.\n"
            "Можете сохранить файлы и вернуться в главное меню.",
            reply_markup=main_menu_kb(),
        )

    except Exception as ex:
        logger.exception("TU: ошибка формирования ТУ (без исходящего): %s", ex)
        await call.message.answer(
            f"Произошла ошибка при формировании ТУ: {ex}\n"
            "Попробуйте ещё раз.",
            reply_markup=main_menu_kb(),
        )
    finally:
        await state.clear()


@tu_router.callback_query(TUStates.WAIT_ACTION, F.data == "tu:with_outgoing")
async def tu_with_outgoing(call: CallbackQuery, state: FSMContext):
    """
    Вариант: подготовить ТУ с исходящим номером и датой.

    - Для каждого шаблона:
        * берётся следующий исходящий номер (на 1 больше максимального в журнале)
        * добавляется строка в журнал с указанием РСО
        * формируется DOCX с этим номером и сегодняшней датой
    """
    await call.answer()

    data = await state.get_data()
    incoming = data.get("incoming") or ""
    egrn = _egrn_from_state(data)

    await call.message.answer(
        "Готовлю ТУ с исходящим номером и датой, обновляю журнал..."
    )

    try:
        docs = build_tu_docs_with_outgoing(egrn, incoming)

        if not docs:
            await call.message.answer(
                "Не удалось найти шаблоны запросов ТУ.\n"
                "Проверьте, что файлы-шаблоны лежат в папке `templates/tu`.",
                reply_markup=main_menu_kb(),
            )
            await state.clear()
            return

        await _send_tu_docs(call.message, docs)

        await call.message.answer(
            "Запросы ТУ сформированы.\n"
            "Можете сохранить файлы и вернуться в главное меню.",
            reply_markup=main_menu_kb(),
        )

    except Exception as ex:
        logger.exception("TU: ошибка формирования ТУ (с исходящим): %s", ex)
        await call.message.answer(
            f"Произошла ошибка при формировании ТУ с исходящим номером: {ex}\n"
            "Проверьте журнал и попробуйте ещё раз.",
            reply_markup=main_menu_kb(),
        )
    finally:
        await state.clear()


@tu_router.message(TUStates.WAIT_EGRN)
async def tu_waiting_egrn_fallback(m: Message, state: FSMContext):
    """
    Если пользователь прислал что-то, но не документ, на шаге ожидания ЕГРН.
    """
    await m.answer(
        "Сейчас я жду файл выписки ЕГРН (XML или ZIP).\n"
        "Пожалуйста, прикрепите файл выписки.",
    )


@tu_router.message(TUStates.WAIT_ACTION)
async def tu_waiting_action_fallback(m: Message, state: FSMContext):
    """
    Если после вывода данных по ЗУ пользователь пишет текст,
    а не выбирает кнопку.
    """
    await m.answer(
        "Пожалуйста, выберите один из вариантов подготовки ТУ с помощью кнопок ниже.",
        reply_markup=_tu_mode_keyboard().as_markup(),
    )
