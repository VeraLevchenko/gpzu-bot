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
from generator.tu_requests_builder import build_tu_docs

logger = logging.getLogger("gpzu-bot.tu")

tu_router = Router()


# ----------------------------- СОСТОЯНИЯ ----------------------------- #
class TUStates(StatesGroup):
    WAIT_INCOMING = State()  # ждём строку вида "124245 от 01.11.2025"
    WAIT_EGRN = State()      # ждём файл выписки и нажатие "Подготовить ТУ"


# --------------------------- КЛАВИАТУРЫ --------------------------- #
def _next_button() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Далее", callback_data="tu:next_to_egrn")
    kb.adjust(1)
    return kb


def _prepare_tu_button() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Подготовить ТУ", callback_data="tu:prepare")
    kb.adjust(1)
    return kb


# --------------------------- ХЕНДЛЕРЫ --------------------------- #
@tu_router.message(F.text == "3. Подготовить запросы ТУ")
async def tu_entry(m: Message, state: FSMContext):
    """
    Точка входа в сценарий "Подготовить запросы ТУ".
    1) Просим ввести входящий номер и дату.
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
    сохраняем её в состоянии и предлагаем нажать «Далее».
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
    Кнопка «Подготовить ТУ» здесь пока НЕ показывается.
    """
    await call.answer()

    data = await state.get_data()
    incoming = data.get("incoming") or "не заполнено"
    logger.info("TU: входящие данные: %s", incoming)

    await state.set_state(TUStates.WAIT_EGRN)
    await call.message.answer(
        "Шаг 2. Прикрепите выписку из ЕГРН на земельный участок "
        "в формате *.xml* или *.zip* (файл выписки).\n\n"
        "После загрузки файла станет доступна кнопка «Подготовить ТУ».",
        parse_mode="Markdown",
    )


@tu_router.message(TUStates.WAIT_EGRN, F.document)
async def tu_got_egrn(m: Message, state: FSMContext):
    """
    Принимаем файл выписки (xml или zip) и сохраняем его метаданные в состояние.
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

    await state.update_data(
        egrn_file_id=doc.file_id,
        egrn_filename=doc.file_name,
        processing=False,
    )
    logger.info("TU: получен файл ЕГРН: %s (%s)", doc.file_name, doc.file_id)

    await m.answer(
        f"Файл выписки получен: *{doc.file_name}*.\n"
        "Теперь можете нажать кнопку «Подготовить ТУ».",
        parse_mode="Markdown",
        reply_markup=_prepare_tu_button().as_markup(),
    )


@tu_router.callback_query(TUStates.WAIT_EGRN, F.data == "tu:prepare")
async def tu_prepare(call: CallbackQuery, state: FSMContext):
    """
    Скачиваем выписку, разбираем её, проверяем что это ЗУ,
    вытаскиваем КН, площадь, ВРИ и адрес и формируем 3 DOCX по шаблонам.
    """
    await call.answer()

    data = await state.get_data()
    processing = data.get("processing", False)
    if processing:
        await call.answer("Запросы ТУ уже подготавливаются, подождите…", show_alert=False)
        return

    file_id = data.get("egrn_file_id")
    filename = data.get("egrn_filename") or "без имени"
    incoming = data.get("incoming") or "не заполнено"

    if not file_id:
        await call.message.answer(
            "Сначала прикрепите выписку из ЕГРН (XML или ZIP), "
            "а затем снова нажмите «Подготовить ТУ»."
        )
        return

    await state.update_data(processing=True)
    await call.message.answer("Начинаю подготовку запросов ТУ, подождите...")

    try:
        # 1. Скачиваем файл с серверов Telegram
        file = await call.message.bot.get_file(file_id)
        egrn_bytes = await download_with_retries(call.message.bot, file.file_path)
        logger.info(
            "TU: скачан файл ЕГРН для ТУ: %s (%d байт), входящий=%s",
            filename,
            len(egrn_bytes),
            incoming,
        )

        # 2. Разбираем выписку через общий парсер (XML или ZIP — не важно)
        try:
            egrn: EGRNData = parse_egrn_xml(egrn_bytes)
        except Exception as ex:
            logger.exception("TU: ошибка парсинга ЕГРН: %s", ex)
            await call.message.answer(
                f"Не удалось разобрать выписку ЕГРН: {ex}\n"
                "Проверьте, что приложен корректный XML/ZIP-файл."
            )
            await state.clear()
            return

        # 3. Проверяем, что это именно ЗУ
        if not egrn.is_land:
            await call.message.answer(
                "Это не выписка ЕГРН по земельному участку.\n"
                "Пожалуйста, прикрепите выписку на земельный участок."
            )
            await state.clear()
            return

        # 4. Собираем нужные данные
        cadnum = egrn.cadnum or "—"
        area = egrn.area or "—"
        vri = egrn.permitted_use or "—"
        address = egrn.address or "—"

        summary_lines = [
            "Данные по земельному участку из ЕГРН:",
            f"*Кадастровый номер:* `{cadnum}`",
            f"*Площадь:* {area} кв. м",
            f"*ВРИ:* {vri}",
            f"*Адрес:* {address}",
            "",
            "Формирую запросы ТУ...",
        ]
        await call.message.answer("\n".join(summary_lines), parse_mode="Markdown")

        # 5. Строим три DOCX по шаблонам
        docs = build_tu_docs(egrn, incoming)

        if not docs:
            await call.message.answer(
                "Не удалось найти шаблоны запросов ТУ.\n"
                "Проверьте, что файлы-шаблоны лежат в папке `templates/tu`.",
                reply_markup=main_menu_kb(),
            )
            await state.clear()
            return

        # 6. Отправляем каждый документ как отдельный файл
        for doc_name, content in docs:
            tmp_path = None
            try:
                with tempfile.NamedTemporaryFile(delete=False, suffix=".docx") as tmp:
                    tmp.write(content)
                    tmp_path = tmp.name

                await call.message.answer_document(
                    FSInputFile(tmp_path, filename=doc_name)
                )
                logger.info("TU: отправлен файл %s", doc_name)

            finally:
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.remove(tmp_path)
                    except Exception:
                        pass

        await call.message.answer(
            "Запросы ТУ сформированы.\n"
            "Можете сохранить файлы и вернуться в главное меню.",
            reply_markup=main_menu_kb(),
        )

    except Exception as ex:
        logger.exception("TU: ошибка обработки ТУ: %s", ex)
        await call.message.answer(
            f"Произошла ошибка при обработке выписки: {ex}\n"
            "Попробуйте ещё раз или пришлите другой файл."
        )
    finally:
        await state.clear()


@tu_router.message(TUStates.WAIT_EGRN)
async def tu_waiting_egrn_fallback(m: Message, state: FSMContext):
    """
    Если пользователь прислал не документ на шаге ожидания ЕГРН.
    """
    await m.answer(
        "Сейчас я жду файл выписки ЕГРН (XML или ZIP).\n"
        "Пожалуйста, прикрепите файл, а затем нажмите «Подготовить ТУ»."
    )
