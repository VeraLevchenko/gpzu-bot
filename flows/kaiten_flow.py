# flows/kaiten_flow.py
from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Dict, Any

from aiogram import Router, F
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    Message,
    CallbackQuery,
    Document as TgDocument,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.utils import download_with_retries
from parsers.application_parser import ApplicationData, parse_application_docx
from core.config import (
    KAITEN_DOMAIN,
    KAITEN_SPACE_ID,
    KAITEN_BOARD_ID,
    KAITEN_FIELD_CADNUM,
    KAITEN_FIELD_SUBMIT_METHOD,
    KAITEN_SUBMIT_METHOD_EPGU,
    KAITEN_FIELD_INCOMING_DATE,
)
from utils.kaiten_service import create_card, upload_attachment

logger = logging.getLogger("gpzu-bot.kaiten")

kaiten_router = Router()


# ----------------------------- СОСТОЯНИЯ ----------------------------- #
class KaitenStates(StatesGroup):
    WAIT_STATEMENT_DOC = State()   # ждём заявление .docx
    WAIT_CONFIRMATION = State()    # ждём подтверждения отправки в Kaiten


# ----------------------------- КЛАВИАТУРЫ ----------------------------- #
def _confirm_creation_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Создать задачу в Кайтен", callback_data="kaiten:create_task")
    kb.button(text="❌ Отмена", callback_data="kaiten:cancel")
    kb.adjust(1)
    return kb


# ----------------------------- ВХОД В СЦЕНАРИЙ ----------------------------- #
@kaiten_router.message(F.text == "1. Создать задачу Кайтен")
async def kaiten_entry(m: Message, state: FSMContext):
    """
    Старт сценария создания задачи Кайтен.
    """
    await state.clear()
    await state.set_state(KaitenStates.WAIT_STATEMENT_DOC)

    await m.answer(
        "Создание задачи Кайтен.\n\n"
        "Шаг 1. Прикрепите файл заявления в формате *.docx*.",
        parse_mode="Markdown",
    )


# ------------------------ ШАГ 1: ЗАЯВЛЕНИЕ .DOCX ------------------------ #
@kaiten_router.message(KaitenStates.WAIT_STATEMENT_DOC, F.document)
async def kaiten_got_statement(m: Message, state: FSMContext):
    """
    Принимаем файл заявления .docx, скачиваем, парсим, сохраняем результат
    и сразу переходим к подтверждению создания задачи (без приложений).
    """
    doc: TgDocument = m.document

    if not (doc.file_name and doc.file_name.lower().endswith(".docx")):
        await m.answer(
            "Пожалуйста, пришлите заявление в формате *.docx*.",
            parse_mode="Markdown",
        )
        return

    # Скачиваем файл заявления
    try:
        file = await m.bot.get_file(doc.file_id)
        doc_bytes = await download_with_retries(m.bot, file.file_path)
    except Exception as ex:
        logger.exception("Kaiten: ошибка скачивания файла заявления: %s", ex)
        await m.answer("Не удалось скачать файл заявления. Попробуйте отправить файл ещё раз.")
        return

    # Парсим заявление
    try:
        app_data: ApplicationData = parse_application_docx(doc_bytes)
    except Exception as ex:
        logger.exception("Kaiten: ошибка парсинга заявления: %s", ex)
        await m.answer("Не удалось разобрать текст заявления. Проверьте, что файл является .docx-документом заявления.")
        return

    # Сохраняем данные для дальнейших шагов
    await state.update_data(
        statement_file_id=doc.file_id,
        statement_file_name=doc.file_name,
        app_data={
            "number": app_data.number,
            "date": app_data.date.isoformat() if app_data.date else None,  # YYYY-MM-DD
            "date_text": app_data.date_text,  # исходный текст даты
            "applicant": app_data.applicant,
            "cadnum": app_data.cadnum,
            "purpose": app_data.purpose,
            "service_date": app_data.service_date.isoformat() if app_data.service_date else None,
        },
    )

    # Сразу показываем сводку и просим подтвердить создание задачи
    await _show_application_summary(m, state)


@kaiten_router.message(KaitenStates.WAIT_STATEMENT_DOC)
async def kaiten_waiting_statement_fallback(m: Message, state: FSMContext):
    """
    Обработка других сообщений в состоянии WAIT_STATEMENT_DOC.
    """
    await m.answer(
        "Сейчас я жду файл заявления в формате *.docx*.",
        parse_mode="Markdown",
    )


# -------------------------- ВЫВОД ИТОГОВ И ПОДТВЕРЖДЕНИЕ -------------------------- #
async def _show_application_summary(msg: Message, state: FSMContext):
    """
    Показываем распарсенные данные и предлагаем подтвердить создание задачи.
    """
    data = await state.get_data()
    app_dict: Dict[str, Any] = data.get("app_data") or {}

    def _fmt_date(iso_str: Optional[str], fallback_text: Optional[str] = None) -> str:
        if iso_str:
            try:
                d = datetime.fromisoformat(iso_str).date()
                return d.strftime("%d.%m.%Y")
            except Exception:
                pass
        return fallback_text or "не удалось определить"

    number = app_dict.get("number") or "б/н"
    date_txt = _fmt_date(app_dict.get("date"), app_dict.get("date_text"))
    applicant = app_dict.get("applicant") or "Не определён"
    cadnum = app_dict.get("cadnum") or "—"
    purpose = app_dict.get("purpose") or "—"
    service_date_txt = _fmt_date(app_dict.get("service_date"))

    # Формируем планируемое название карточки: "<номер> <заявитель>"
    if app_dict.get("number") and app_dict.get("applicant"):
        title_preview = f"{app_dict['number']} {applicant}"
    elif app_dict.get("number"):
        title_preview = app_dict["number"]
    else:
        title_preview = applicant

    text = (
        "📊 *Проверьте данные перед созданием задачи:*\n\n"
        f"📄 *Заявление №:* {number}\n"
        f"📅 *Дата заявления:* {date_txt}\n"
        f"👤 *Заявитель:* {applicant}\n"
        f"🗺 *Кадастровый номер:* {cadnum}\n"
        f"🗺 *Цель ЗУ:* {purpose}\n"
        f"📅 *Срок (план):* {service_date_txt}\n\n"
        f"Название задачи в Kaiten будет: *{title_preview}*\n"
        "Создать карточку?"
    )

    await state.set_state(KaitenStates.WAIT_CONFIRMATION)
    await msg.answer(
        text,
        reply_markup=_confirm_creation_keyboard().as_markup(),
        parse_mode="Markdown",
    )


# -------------------------- СОЗДАНИЕ ЗАДАЧИ (API) -------------------------- #
@kaiten_router.callback_query(KaitenStates.WAIT_CONFIRMATION, F.data == "kaiten:create_task")
async def kaiten_create_task_handler(call: CallbackQuery, state: FSMContext):
    """
    Создаем карточку в Kaiten и загружаем файл заявления.
    """
    await call.message.edit_text("⏳ Создаю задачу в Кайтен, загружаю заявление...")

    data = await state.get_data()
    app_dict = data.get("app_data", {})

    # 1. Подготовка данных
    applicant = app_dict.get("applicant") or "Неизвестный заявитель"
    number = app_dict.get("number")
    cadnum = app_dict.get("cadnum") or "—"
    purpose = app_dict.get("purpose") or "—"
    date_stmt = app_dict.get("date_text") or "—"
    service_date_iso = app_dict.get("service_date")  # YYYY-MM-DD или None

    # Заголовок карточки: "<номер> <заявитель>"
    if number and applicant:
        title = f"{number} {applicant}"
    elif number:
        title = number
    else:
        title = applicant

    # Описание карточки
    description = (
        f"**Заявление №:** {number or 'б/н'}\n"
        f"**Заявитель:** {applicant}\n"
        f"**Кадастровый номер:** {cadnum}\n"
        f"**Цель:** {purpose}\n"
        f"**Дата заявления:** {date_stmt}\n\n"
        "created by telegram bot"
    )

    # --- КАСТОМНЫЕ ПОЛЯ KAITEN ---
    properties: Dict[str, Any] = {}

    # 1. Исх_данные 1 Кадастровый номер = кадастровый номер ЗУ
    if KAITEN_FIELD_CADNUM and cadnum and cadnum != "—":
        properties[KAITEN_FIELD_CADNUM] = cadnum

    # 2. Способ подачи = ЕПГУ (поле-справочник, массив с ID варианта)
    if KAITEN_FIELD_SUBMIT_METHOD and KAITEN_SUBMIT_METHOD_EPGU:
        properties[KAITEN_FIELD_SUBMIT_METHOD] = [KAITEN_SUBMIT_METHOD_EPGU]

    # 3. Входящая дата = дата заявления (как объект { "date": "YYYY-MM-DD", "time": null, "tzOffset": null })
    incoming_iso: Optional[str] = None
    if app_dict.get("date"):
        # уже isoformat 'YYYY-MM-DD' из app_data.date.isoformat()
        incoming_iso = app_dict["date"]
    else:
        # пробуем распарсить текстовую дату вида "01.11.2025", если iso нет
        date_text = app_dict.get("date_text")
        if date_text:
            for fmt in ("%d.%m.%Y", "%d-%m-%Y", "%Y-%m-%d"):
                try:
                    d = datetime.strptime(date_text, fmt).date()
                    incoming_iso = d.isoformat()
                    break
                except Exception:
                    continue

    if KAITEN_FIELD_INCOMING_DATE and incoming_iso:
        properties[KAITEN_FIELD_INCOMING_DATE] = {
            "date": incoming_iso,
            "time": None,
            "tzOffset": None,
        }
    # ------------------------------

    # 2. Создание карточки
    card_id = await create_card(
        title=title,
        description=description,
        due_date=service_date_iso,
        properties=properties or None,
    )

    if not card_id:
        await call.message.edit_text(
            "❌ Ошибка: не удалось создать карточку в Kaiten. Проверьте токены и ID."
        )
        await state.clear()
        return

    # 3. Загрузка файла заявления
    uploaded_info = []

    stmt_fid = data.get("statement_file_id")
    stmt_name = data.get("statement_file_name", "statement.docx")
    if stmt_fid:
        try:
            f_info = await call.bot.get_file(stmt_fid)
            f_bytes = await download_with_retries(call.bot, f_info.file_path)
            if await upload_attachment(card_id, stmt_name, f_bytes):
                uploaded_info.append("Заявление")
        except Exception as e:
            logger.error(f"Ошибка загрузки заявления: {e}")

    # 4. Ссылка на карточку
    card_url = (
        f"https://{KAITEN_DOMAIN}"
        f"/space/{KAITEN_SPACE_ID}"
        f"/boards/card/{card_id}"
    )

    res_text = (
        f"✅ *Задача успешно создана!*\n"
        f"ID: `{card_id}`\n"
        f"Файлы: {', '.join(uploaded_info) if uploaded_info else 'нет'}\n\n"
        f"[Открыть карточку в Kaiten]({card_url})"
    )

    await call.message.edit_text(
        res_text,
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )
    await state.clear()


@kaiten_router.callback_query(KaitenStates.WAIT_CONFIRMATION, F.data == "kaiten:cancel")
async def kaiten_cancel_handler(call: CallbackQuery, state: FSMContext):
    """
    Отмена создания задачи.
    """
    await call.message.edit_text("❌ Создание задачи отменено.")
    await state.clear()
