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

logger = logging.getLogger("gpzu-bot.kaiten")

kaiten_router = Router()


# ----------------------------- СОСТОЯНИЯ ----------------------------- #
class KaitenStates(StatesGroup):
    WAIT_STATEMENT_DOC = State()   # ждём заявление .docx
    WAIT_ATTACH_ARCHIVE = State()  # ждём архив с приложениями или "Без приложений"


# ----------------------------- КЛАВИАТУРЫ ----------------------------- #
def _skip_archive_keyboard() -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    kb.button(text="Продолжить без приложений", callback_data="kaiten:skip_archive")
    kb.adjust(1)
    return kb


# ----------------------------- ВХОД В СЦЕНАРИЙ ----------------------------- #
@kaiten_router.message(F.text == "1. Создать задачу Кайтен")
async def kaiten_entry(m: Message, state: FSMContext):
    """
    Старт сценария создания задачи Кайтен (первый этап — разбор заявления).
    """
    await state.clear()
    await state.set_state(KaitenStates.WAIT_STATEMENT_DOC)

    await m.answer(
        "Создание задачи Кайтен.\n\n"
        "Шаг 1. Прикрепите файл заявления в формате *.docx*.\n"
        "Я разберу текст заявления и позже покажу:\n"
        "• номер заявления\n"
        "• дату заявления\n"
        "• заявителя\n"
        "• кадастровый номер участка\n"
        "• цель использования земельного участка\n"
        "• дату оказания услуги (дата заявления + 14 рабочих дней).",
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
        logger.info(
            "Kaiten: получен файл заявления: %s (%d байт)",
            doc.file_name,
            len(doc_bytes),
        )
    except Exception as ex:
        logger.exception("Kaiten: ошибка скачивания файла заявления: %s", ex)
        await m.answer(
            f"Не удалось скачать файл заявления: {ex}\n"
            f"Попробуйте отправить файл ещё раз.",
        )
        return

    # Парсим заявление (упрощённый парсер под твою форму)
    try:
        app_data: ApplicationData = parse_application_docx(doc_bytes)
    except Exception as ex:
        logger.exception("Kaiten: ошибка парсинга заявления: %s", ex)
        await m.answer(
            f"Не удалось разобрать текст заявления: {ex}\n"
            f"Проверьте, что файл действительно является .docx-документом заявления.",
        )
        return

    # Сохраняем всё в состояние (для дальнейшего использования, в т.ч. для Кайтен)
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
        "Если приложений нет или вы не хотите их отправлять, нажмите кнопку "
        "«Продолжить без приложений».",
        reply_markup=_skip_archive_keyboard().as_markup(),
    )


@kaiten_router.message(KaitenStates.WAIT_STATEMENT_DOC)
async def kaiten_waiting_statement_fallback(m: Message, state: FSMContext):
    """
    Любые другие сообщения в состоянии WAIT_STATEMENT_DOC.
    """
    await m.answer(
        "Сейчас я жду файл заявления в формате *.docx*.",
        parse_mode="Markdown",
    )


# ------------------------ ШАГ 2: ПРИЛОЖЕНИЯ (АРХИВ) ------------------------ #
@kaiten_router.message(KaitenStates.WAIT_ATTACH_ARCHIVE, F.document)
async def kaiten_got_archive(m: Message, state: FSMContext):
    """
    Принимаем архив с приложениями (не обязательно). Сохраняем file_id и далее
    показываем пользователю распарсенные данные заявления.
    """
    doc: TgDocument = m.document

    # Разрешим основные архивные расширения
    if not (
        doc.file_name
        and doc.file_name.lower().endswith(
            (".zip", ".rar", ".7z", ".7zip", ".tar", ".gz")
        )
    ):
        await m.answer(
            "Похоже, это не архив.\n"
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
    Пользователь решил продолжить без приложений.
    """
    await call.answer()
    await _show_application_summary(call.message, state)


@kaiten_router.message(KaitenStates.WAIT_ATTACH_ARCHIVE)
async def kaiten_waiting_archive_fallback(m: Message, state: FSMContext):
    """
    Любые другие сообщения в состоянии WAIT_ATTACH_ARCHIVE.
    """
    await m.answer(
        "Сейчас я жду архив с приложениями (например, *.zip*), "
        "или нажмите «Продолжить без приложений».",
        reply_markup=_skip_archive_keyboard().as_markup(),
        parse_mode="Markdown",
    )


# -------------------------- ВЫВОД ИТОГОВ ПАРСИНГА -------------------------- #
async def _show_application_summary(msg: Message, state: FSMContext):
    """
    Показываем пользователю распарсенные данные заявления
    и дату оказания услуги (дата + 14 рабочих дней).
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

    number = app_dict.get("number") or "не удалось определить"
    date_txt = _fmt_date(app_dict.get("date"), app_dict.get("date_text"))
    applicant = app_dict.get("applicant") or "не удалось определить"
    cadnum = app_dict.get("cadnum") or "не удалось определить"
    purpose = app_dict.get("purpose") or "не удалось определить"
    service_date_txt = _fmt_date(app_dict.get("service_date"))

    text = (
        "Результат разбора заявления:\n\n"
        f"Номер заявления: {number}\n"
        f"Дата заявления: {date_txt}\n"
        f"Заявитель: {applicant}\n"
        f"Кадастровый номер ЗУ: {cadnum}\n"
        f"Цель использования ЗУ: {purpose}\n"
        f"Дата оказания услуги (14 рабочих дней): {service_date_txt}\n\n"
        "На следующем шаге можно будет создать задачу в Кайтен с этими данными."
    )

    await msg.answer(text)
    # Пока по завершении сценария очищаем состояние.
    # Когда будем добавлять создание задачи Кайтен, можно будет оставить данные.
    await state.clear()
