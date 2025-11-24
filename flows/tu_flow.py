# flows/tu_flow.py
"""
Сценарий подготовки запросов технических условий (ТУ).

НОВЫЙ АЛГОРИТМ:

1. Пользователь выбирает способ ввода данных:
   - Прикрепить заявление (DOCX) - парсится автоматически
   - Ввести данные вручную

2. Если прикреплено заявление:
   - Парсим номер, дату, заявителя
   - Просим прикрепить выписку ЕГРН
   - Парсим кадастровый номер, адрес, площадь, ВРИ
   - Формируем ТУ с регистрацией

3. Если ввод вручную:
   - Просим номер заявления
   - Просим дату заявления
   - Просим заявителя
   - Просим кадастровый номер
   - Просим адрес (опционально - можно взять из ЕГРН)
   - Просим выписку ЕГРН для площади и ВРИ
   - Формируем ТУ с регистрацией
"""

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

from core.utils import download_with_retries
from parsers.egrn_parser import parse_egrn_xml, EGRNData
from parsers.application_parser import parse_application_docx, ApplicationData
from generator.tu_requests_builder import build_tu_docs_with_outgoing

logger = logging.getLogger("gpzu-bot.tu")

tu_router = Router()


# ----------------------------- СОСТОЯНИЯ ----------------------------- #
class TUStates(StatesGroup):
    WAIT_INPUT_METHOD = State()  # выбор: заявление или ручной ввод
    
    # Ветка: прикрепить заявление
    WAIT_APPLICATION_DOC = State()  # ждём файл заявления
    WAIT_EGRN_AFTER_APP = State()   # ждём ЕГРН после заявления
    
    # Ветка: ручной ввод
    WAIT_MANUAL_APP_NUM = State()    # номер заявления
    WAIT_MANUAL_APP_DATE = State()   # дата заявления
    WAIT_MANUAL_APPLICANT = State()  # заявитель
    WAIT_MANUAL_CADNUM = State()     # кадастровый номер
    WAIT_MANUAL_ADDRESS = State()    # адрес (опционально)
    WAIT_MANUAL_EGRN = State()       # выписка ЕГРН


# --------------------------- КЛАВИАТУРЫ --------------------------- #
def _input_method_keyboard() -> InlineKeyboardBuilder:
    """Выбор способа ввода данных."""
    kb = InlineKeyboardBuilder()
    kb.button(text="📄 Прикрепить заявление (DOCX)", callback_data="tu:attach_app")
    kb.button(text="⌨️ Ввести данные вручную", callback_data="tu:manual")
    kb.adjust(1)
    return kb


def _skip_address_keyboard() -> InlineKeyboardBuilder:
    """Кнопка пропустить адрес."""
    kb = InlineKeyboardBuilder()
    kb.button(text="Пропустить (взять из ЕГРН)", callback_data="tu:skip_address")
    kb.adjust(1)
    return kb


# ------------------------------ ВХОД В СЦЕНАРИЙ ------------------------------ #
@tu_router.message(F.text == "3. Подготовить запросы ТУ")
async def tu_entry(m: Message, state: FSMContext):
    """Старт сценария подготовки запросов ТУ."""
    await state.clear()
    await state.set_state(TUStates.WAIT_INPUT_METHOD)

    await m.answer(
        "🔧 Подготовка запросов ТУ\n\n"
        "Выберите способ ввода данных:",
        reply_markup=_input_method_keyboard().as_markup(),
    )


# ==================== ВЕТКА 1: ПРИКРЕПИТЬ ЗАЯВЛЕНИЕ ==================== #

@tu_router.callback_query(TUStates.WAIT_INPUT_METHOD, F.data == "tu:attach_app")
async def tu_chose_attach_app(call: CallbackQuery, state: FSMContext):
    """Пользователь выбрал прикрепить заявление."""
    await call.answer()
    await state.set_state(TUStates.WAIT_APPLICATION_DOC)
    
    await call.message.answer(
        "📄 Прикрепите заявление о выдаче ГПЗУ в формате DOCX.\n\n"
        "Я автоматически извлеку из него:\n"
        "• Номер заявления\n"
        "• Дату заявления\n"
        "• Заявителя\n"
        "• Кадастровый номер\n\n"
        "После этого попрошу прикрепить выписку ЕГРН."
    )


@tu_router.message(TUStates.WAIT_APPLICATION_DOC, F.document)
async def tu_got_application(m: Message, state: FSMContext):
    """Получено заявление - парсим его."""
    doc: TgDocument = m.document
    
    if not doc.file_name or not doc.file_name.lower().endswith(".docx"):
        await m.answer("❌ Это не DOCX-файл. Пожалуйста, прикрепите заявление в формате DOCX.")
        return
    
    # Скачиваем
    try:
        file = await m.bot.get_file(doc.file_id)
        app_bytes = await download_with_retries(m.bot, file.file_path)
        logger.info("TU: получено заявление: %s (%d байт)", doc.file_name, len(app_bytes))
    except Exception as ex:
        logger.exception("TU: ошибка скачивания заявления: %s", ex)
        await m.answer(f"❌ Не удалось скачать файл: {ex}")
        return
    
    # Парсим
    try:
        app_data: ApplicationData = parse_application_docx(app_bytes)
    except Exception as ex:
        logger.exception("TU: ошибка парсинга заявления: %s", ex)
        await m.answer(f"❌ Не удалось разобрать заявление: {ex}")
        return
    
    # Проверяем, что извлечены основные данные
    if not app_data.number:
        await m.answer(
            "⚠️ Не удалось извлечь номер заявления из документа.\n"
            "Проверьте формат файла или используйте ручной ввод."
        )
        return
    
    # Сохраняем данные
    await state.update_data(
        app_number=app_data.number or "",
        app_date=app_data.date_text or "",
        applicant=app_data.applicant or "",
        cadnum=app_data.cadnum or "",
    )
    
    # Показываем извлечённые данные
    lines = [
        "✅ Заявление успешно обработано!",
        "",
        f"📋 Номер заявления: {app_data.number or '—'}",
        f"📅 Дата заявления: {app_data.date_text or '—'}",
        f"👤 Заявитель: {app_data.applicant or '—'}",
        f"🏞 Кадастровый номер: {app_data.cadnum or '—'}",
    ]
    
    await m.answer("\n".join(lines))
    
    # Переходим к запросу ЕГРН
    await state.set_state(TUStates.WAIT_EGRN_AFTER_APP)
    await m.answer(
        "📎 Теперь прикрепите выписку из ЕГРН на земельный участок "
        "в формате XML или ZIP.\n\n"
        "Из выписки я извлеку адрес, площадь и ВРИ."
    )


@tu_router.message(TUStates.WAIT_APPLICATION_DOC)
async def tu_waiting_app_fallback(m: Message, state: FSMContext):
    """Ожидается файл заявления."""
    await m.answer("📄 Пожалуйста, прикрепите заявление в формате DOCX.")


@tu_router.message(TUStates.WAIT_EGRN_AFTER_APP, F.document)
async def tu_got_egrn_after_app(m: Message, state: FSMContext):
    """Получена выписка ЕГРН после заявления - завершаем формирование."""
    doc: TgDocument = m.document
    
    if not doc.file_name or not (
        doc.file_name.lower().endswith(".xml") or doc.file_name.lower().endswith(".zip")
    ):
        await m.answer("❌ Это не XML/ZIP-файл. Пожалуйста, прикрепите выписку ЕГРН.")
        return
    
    # Скачиваем
    try:
        file = await m.bot.get_file(doc.file_id)
        egrn_bytes = await download_with_retries(m.bot, file.file_path)
        logger.info("TU: получена выписка ЕГРН: %s (%d байт)", doc.file_name, len(egrn_bytes))
    except Exception as ex:
        logger.exception("TU: ошибка скачивания ЕГРН: %s", ex)
        await m.answer(f"❌ Не удалось скачать файл: {ex}")
        return
    
    # Парсим
    try:
        egrn: EGRNData = parse_egrn_xml(egrn_bytes)
    except Exception as ex:
        logger.exception("TU: ошибка парсинга ЕГРН: %s", ex)
        await m.answer(f"❌ Не удалось разобрать выписку ЕГРН: {ex}")
        return
    
    # Проверяем, что это ЗУ
    if not egrn.is_land:
        await m.answer("❌ Это не выписка ЕГРН по земельному участку.")
        return
    
    # Получаем данные из состояния
    data = await state.get_data()
    
    # Формируем ТУ
    await m.answer("⚙️ Формирую запросы ТУ с регистрацией...\nПожалуйста, подождите...")
    
    try:
        docs = build_tu_docs_with_outgoing(
            cadnum=data.get("cadnum") or egrn.cadnum or "",
            address=egrn.address or "",
            area=egrn.area or "",
            vri=egrn.permitted_use or "",
            app_number=data.get("app_number", ""),
            app_date=data.get("app_date", ""),
            applicant=data.get("applicant", ""),
        )
    except Exception as ex:
        logger.exception("TU: ошибка формирования ТУ: %s", ex)
        await m.answer(f"❌ Не удалось сформировать запросы ТУ:\n{ex}")
        await state.clear()
        return
    
    # Отправляем документы
    for filename, file_bytes in docs:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
        try:
            tmp.write(file_bytes)
            tmp.flush()
            tmp.close()
            await m.answer_document(FSInputFile(tmp.name, filename=filename))
        finally:
            try:
                os.remove(tmp.name)
            except Exception:
                pass
    
    await m.answer(
        "✅ Запросы ТУ успешно сформированы и зарегистрированы!\n"
        "Можете вернуться в главное меню."
    )
    await state.clear()


@tu_router.message(TUStates.WAIT_EGRN_AFTER_APP)
async def tu_waiting_egrn_after_app_fallback(m: Message, state: FSMContext):
    """Ожидается выписка ЕГРН."""
    await m.answer("📎 Пожалуйста, прикрепите выписку ЕГРН (XML или ZIP).")


# ==================== ВЕТКА 2: РУЧНОЙ ВВОД ==================== #

@tu_router.callback_query(TUStates.WAIT_INPUT_METHOD, F.data == "tu:manual")
async def tu_chose_manual(call: CallbackQuery, state: FSMContext):
    """Пользователь выбрал ручной ввод."""
    await call.answer()
    await state.set_state(TUStates.WAIT_MANUAL_APP_NUM)
    
    await call.message.answer(
        "⌨️ Ручной ввод данных\n\n"
        "Шаг 1/5: Введите номер заявления (например, 6422028095):"
    )


@tu_router.message(TUStates.WAIT_MANUAL_APP_NUM, F.text)
async def tu_got_manual_app_num(m: Message, state: FSMContext):
    """Получен номер заявления."""
    app_num = (m.text or "").strip()
    if not app_num:
        await m.answer("❌ Номер заявления не может быть пустым. Попробуйте ещё раз:")
        return
    
    await state.update_data(app_number=app_num)
    await state.set_state(TUStates.WAIT_MANUAL_APP_DATE)
    
    await m.answer(
        f"✅ Номер заявления: {app_num}\n\n"
        "Шаг 2/5: Введите дату заявления (например, 15.11.2025):"
    )


@tu_router.message(TUStates.WAIT_MANUAL_APP_DATE, F.text)
async def tu_got_manual_app_date(m: Message, state: FSMContext):
    """Получена дата заявления."""
    app_date = (m.text or "").strip()
    if not app_date:
        await m.answer("❌ Дата заявления не может быть пустой. Попробуйте ещё раз:")
        return
    
    await state.update_data(app_date=app_date)
    await state.set_state(TUStates.WAIT_MANUAL_APPLICANT)
    
    await m.answer(
        f"✅ Дата заявления: {app_date}\n\n"
        "Шаг 3/5: Введите заявителя (ФИО или наименование организации):"
    )


@tu_router.message(TUStates.WAIT_MANUAL_APPLICANT, F.text)
async def tu_got_manual_applicant(m: Message, state: FSMContext):
    """Получен заявитель."""
    applicant = (m.text or "").strip()
    if not applicant:
        await m.answer("❌ Заявитель не может быть пустым. Попробуйте ещё раз:")
        return
    
    await state.update_data(applicant=applicant)
    await state.set_state(TUStates.WAIT_MANUAL_CADNUM)
    
    await m.answer(
        f"✅ Заявитель: {applicant}\n\n"
        "Шаг 4/5: Введите кадастровый номер земельного участка (например, 42:30:000000:1234):"
    )


@tu_router.message(TUStates.WAIT_MANUAL_CADNUM, F.text)
async def tu_got_manual_cadnum(m: Message, state: FSMContext):
    """Получен кадастровый номер."""
    cadnum = (m.text or "").strip()
    if not cadnum:
        await m.answer("❌ Кадастровый номер не может быть пустым. Попробуйте ещё раз:")
        return
    
    await state.update_data(cadnum=cadnum)
    await state.set_state(TUStates.WAIT_MANUAL_EGRN)
    
    await m.answer(
        f"✅ Кадастровый номер: {cadnum}\n\n"
        "Шаг 5/5: Прикрепите выписку из ЕГРН (XML или ZIP).\n"
        "Из неё я извлеку адрес, площадь и ВРИ."
    )


@tu_router.message(TUStates.WAIT_MANUAL_EGRN, F.document)
async def tu_got_manual_egrn(m: Message, state: FSMContext):
    """Получена выписка ЕГРН при ручном вводе - завершаем формирование."""
    doc: TgDocument = m.document
    
    if not doc.file_name or not (
        doc.file_name.lower().endswith(".xml") or doc.file_name.lower().endswith(".zip")
    ):
        await m.answer("❌ Это не XML/ZIP-файл. Пожалуйста, прикрепите выписку ЕГРН.")
        return
    
    # Скачиваем
    try:
        file = await m.bot.get_file(doc.file_id)
        egrn_bytes = await download_with_retries(m.bot, file.file_path)
    except Exception as ex:
        logger.exception("TU: ошибка скачивания ЕГРН: %s", ex)
        await m.answer(f"❌ Не удалось скачать файл: {ex}")
        return
    
    # Парсим
    try:
        egrn: EGRNData = parse_egrn_xml(egrn_bytes)
    except Exception as ex:
        logger.exception("TU: ошибка парсинга ЕГРН: %s", ex)
        await m.answer(f"❌ Не удалось разобрать выписку ЕГРН: {ex}")
        return
    
    if not egrn.is_land:
        await m.answer("❌ Это не выписка ЕГРН по земельному участку.")
        return
    
    # Получаем данные
    data = await state.get_data()
    
    # Формируем ТУ
    await m.answer("⚙️ Формирую запросы ТУ с регистрацией...\nПожалуйста, подождите...")
    
    try:
        docs = build_tu_docs_with_outgoing(
            cadnum=data.get("cadnum", ""),
            address=egrn.address or "",
            area=egrn.area or "",
            vri=egrn.permitted_use or "",
            app_number=data.get("app_number", ""),
            app_date=data.get("app_date", ""),
            applicant=data.get("applicant", ""),
        )
    except Exception as ex:
        logger.exception("TU: ошибка формирования ТУ: %s", ex)
        await m.answer(f"❌ Не удалось сформировать запросы ТУ:\n{ex}")
        await state.clear()
        return
    
    # Отправляем документы
    for filename, file_bytes in docs:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
        try:
            tmp.write(file_bytes)
            tmp.flush()
            tmp.close()
            await m.answer_document(FSInputFile(tmp.name, filename=filename))
        finally:
            try:
                os.remove(tmp.name)
            except Exception:
                pass
    
    await m.answer(
        "✅ Запросы ТУ успешно сформированы и зарегистрированы!\n"
        "Можете вернуться в главное меню."
    )
    await state.clear()


@tu_router.message(TUStates.WAIT_MANUAL_EGRN)
async def tu_waiting_manual_egrn_fallback(m: Message, state: FSMContext):
    """Ожидается выписка ЕГРН."""
    await m.answer("📎 Пожалуйста, прикрепите выписку ЕГРН (XML или ZIP).")
