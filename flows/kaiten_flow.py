# flows/kaiten_flow.py
from __future__ import annotations

import logging
from datetime import datetime, date
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

# --- ИМПОРТЫ KAITEN ---
# KAITEN_BOARD_ID остается в импорте, так как нужен для создания карточки
from core.config import KAITEN_DOMAIN, KAITEN_SPACE_ID, KAITEN_BOARD_ID
# Предполагаем, что utils/kaiten_service.py доступен как utils.kaiten_service
from utils.kaiten_service import create_card, upload_attachment
# ----------------------

logger = logging.getLogger("gpzu-bot.kaiten")

kaiten_router = Router()


# ----------------------------- СОСТОЯНИЯ ----------------------------- #
class KaitenStates(StatesGroup):
    WAIT_STATEMENT_DOC = State()   # ждём заявление .docx
    WAIT_ATTACH_ARCHIVE = State()  # ждём архив с приложениями
    WAIT_CONFIRMATION = State()    # ждём подтверждения отправки в Kaiten


# ----------------------------- КЛАВИАТУРЫ ----------------------------- #
def _skip_archive_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Продолжить без приложений", callback_data="kaiten:skip_archive")
    kb.adjust(1)
    return kb

def _confirm_creation_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Создать задачу в Kaiten", callback_data="kaiten:create_task")
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
    и переходим к шагу с приложениями.
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

    # Сохраняем данные
    await state.update_data(
        statement_file_id=doc.file_id,
        statement_file_name=doc.file_name,
        app_data={
            "number": app_data.number,
            "date": app_data.date.isoformat() if app_data.date else None,
            "date_text": app_data.date_text,
            "applicant": app_data.applicant,
            "cadnum": app_data.cadnum,
            "purpose": app_data.purpose,
            "service_date": app_data.service_date.isoformat() if app_data.service_date else None,
        },
    )

    await state.set_state(KaitenStates.WAIT_ATTACH_ARCHIVE)

    await m.answer(
        "Заявление получено и обработано.\n\n"
        "Шаг 2. Прикрепите архив с приложениями к заявлению (например, *.zip*).\n"
        "Если приложений нет, нажмите «Продолжить без приложений».",
        reply_markup=_skip_archive_keyboard().as_markup(),
    )


@kaiten_router.message(KaitenStates.WAIT_STATEMENT_DOC)
async def kaiten_waiting_statement_fallback(m: Message, state: FSMContext):
    """
    Обработка других сообщений в состоянии WAIT_STATEMENT_DOC.
    """
    await m.answer(
        "Сейчас я жду файл заявления в формате *.docx*.",
        parse_mode="Markdown",
    )


# ------------------------ ШАГ 2: ПРИЛОЖЕНИЯ (АРХИВ) ------------------------ #
@kaiten_router.message(KaitenStates.WAIT_ATTACH_ARCHIVE, F.document)
async def kaiten_got_archive(m: Message, state: FSMContext):
    """
    Принимаем архив с приложениями.
    """
    doc: TgDocument = m.document

    if not (
        doc.file_name
        and doc.file_name.lower().endswith(
            (".zip", ".rar", ".7z", ".7zip", ".tar", ".gz")
        )
    ):
        await m.answer(
            "Пожалуйста, прикрепите архив с приложениями "
            "(например, *.zip*), либо нажмите «Продолжить без приложений».",
            reply_markup=_skip_archive_keyboard().as_markup(),
            parse_mode="Markdown",
        )
        return

    await state.update_data(
        archive_file_id=doc.file_id,
        archive_file_name=doc.file_name,
    )

    await _show_application_summary(m, state)


@kaiten_router.callback_query(KaitenStates.WAIT_ATTACH_ARCHIVE, F.data == "kaiten:skip_archive")
async def kaiten_skip_archive(call: CallbackQuery, state: FSMContext):
    """
    Продолжение без приложений.
    """
    await call.answer()
    await _show_application_summary(call.message, state)


@kaiten_router.message(KaitenStates.WAIT_ATTACH_ARCHIVE)
async def kaiten_waiting_archive_fallback(m: Message, state: FSMContext):
    """
    Обработка других сообщений в состоянии WAIT_ATTACH_ARCHIVE.
    """
    await m.answer(
        "Сейчас я жду архив с приложениями (например, *.zip*), "
        "или нажмите «Продолжить без приложений».",
        reply_markup=_skip_archive_keyboard().as_markup(),
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

    text = (
        "📊 *Проверьте данные перед созданием задачи:*\n\n"
        f"📄 *Заявление №:* {number}\n"
        f"📅 *Дата заявления:* {date_txt}\n"
        f"👤 *Заявитель:* {applicant}\n"
        f"🗺 *Кадастровый номер:* {cadnum}\n"
        f"🗺 *Цель ЗУ:* {purpose}\n"
        f"📅 *Срок (план):* {service_date_txt}\n\n"
        f"Название задачи в Kaiten будет: *{applicant}*\n"
        "Создать карточку?"
    )

    await state.set_state(KaitenStates.WAIT_CONFIRMATION)
    await msg.answer(text, reply_markup=_confirm_creation_keyboard().as_markup(), parse_mode="Markdown")


# -------------------------- СОЗДАНИЕ ЗАДАЧИ (API) -------------------------- #
@kaiten_router.callback_query(KaitenStates.WAIT_CONFIRMATION, F.data == "kaiten:create_task")
async def kaiten_create_task_handler(call: CallbackQuery, state: FSMContext):
    """
    Создаем карточку (title=заявитель) и загружаем файлы.
    """
    await call.message.edit_text("⏳ Создаю задачу в Kaiten, загружаю файлы...")

    data = await state.get_data()
    app_dict = data.get("app_data", {})

    # 1. Подготовка данных
    applicant = app_dict.get("applicant") or "Неизвестный заявитель"
    
    # Title карточки — только заявитель
    title = applicant

    # Описание карточки
    number = app_dict.get("number") or "б/н"
    cadnum = app_dict.get("cadnum") or "—"
    purpose = app_dict.get("purpose") or "—"
    date_stmt = app_dict.get("date_text") or "—"
    service_date_iso = app_dict.get("service_date") # YYYY-MM-DD

    description = (
        f"**Заявление №:** {number}\n"
        f"**Заявитель:** {applicant}\n"
        f"**Кадастровый номер:** {cadnum}\n"
        f"**Цель:** {purpose}\n"
        f"**Дата заявления:** {date_stmt}\n\n"
        "created by telegram bot"
    )

    # 2. Создание карточки
    card_id = await create_card(
        title=title,
        description=description,
        due_date=service_date_iso
    )

    if not card_id:
        await call.message.edit_text("❌ Ошибка: не удалось создать карточку в Kaiten. Проверьте токены и ID.")
        await state.clear()
        return

    # 3. Загрузка файлов
    uploaded_info = []
    
    # a) Заявление .docx
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

    # b) Архив .zip
    arch_fid = data.get("archive_file_id")
    arch_name = data.get("archive_file_name", "archive.zip")
    if arch_fid:
        try:
            f_info = await call.bot.get_file(arch_fid)
            f_bytes = await download_with_retries(call.bot, f_info.file_path)
            if await upload_attachment(card_id, arch_name, f_bytes):
                uploaded_info.append("Приложения")
        except Exception as e:
            logger.error(f"Ошибка загрузки архива: {e}")

    # 4. Результат
    # Формируем ссылку в требуемом формате: "https://.../space/{spaceId}/boards/card/{cardId}"
    card_url = (
        f"https://{KAITEN_DOMAIN}"
        f"/space/{KAITEN_SPACE_ID}"
        f"/boards/card/{card_id}" # ID доски удален
    )
    
    res_text = (
        f"✅ *Задача успешно создана!*\n"
        f"ID: `{card_id}`\n"
        f"Файлы: {', '.join(uploaded_info) if uploaded_info else 'нет'}\n\n"
        f"[Открыть карточку в Kaiten]({card_url})"
    )
    
    await call.message.edit_text(res_text, parse_mode="Markdown", disable_web_page_preview=True)
    await state.clear()


@kaiten_router.callback_query(KaitenStates.WAIT_CONFIRMATION, F.data == "kaiten:cancel")
async def kaiten_cancel_handler(call: CallbackQuery, state: FSMContext):
    """
    Отмена создания задачи.
    """
    await call.message.edit_text("❌ Создание задачи отменено.")
    await state.clear()