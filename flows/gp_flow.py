# flows/gp_flow.py
"""
Модуль для создания градостроительного плана (ГП).

Полный функционал:
1. Запрашивает заявление .docx
2. Запрашивает выписку ЕГРН .xml/.zip
3. Выполняет пространственный анализ (зоны, объекты, ограничения)
4. Показывает результаты анализа
5. Генерирует документ ГП
"""

import logging
from typing import Optional, Dict, Any

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.types import Message, Document as TgDocument, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder

from core.keyboards import main_menu_kb
from core.utils import download_with_retries
from parsers.application_parser import ApplicationData, parse_application_docx
from parsers.egrn_parser import parse_egrn_xml, EGRNData
from models.gp_data import GPData, create_gp_data_from_parsed
from utils.spatial_analysis import perform_spatial_analysis, get_analysis_summary

logger = logging.getLogger("gpzu-bot.gp")

gp_router = Router()


# ----------------------------- СОСТОЯНИЯ ----------------------------- #
class GPStates(StatesGroup):
    """Состояния для сценария создания ГП"""
    WAIT_APPLICATION = State()   # ждём заявление .docx
    WAIT_EGRN = State()           # ждём выписку ЕГРН
    ANALYZING = State()           # выполняем анализ
    SHOW_RESULTS = State()        # показываем результаты


# ----------------------------- КЛАВИАТУРЫ ----------------------------- #
def _actions_keyboard() -> InlineKeyboardBuilder:
    """Клавиатура с действиями после анализа"""
    kb = InlineKeyboardBuilder()
    kb.button(text="📄 Сформировать ГП", callback_data="gp:generate")
    kb.button(text="🔄 Начать заново", callback_data="gp:restart")
    kb.button(text="❌ Отмена", callback_data="gp:cancel")
    kb.adjust(1)
    return kb


# ---------------------- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---------------------- #
def _application_to_state(app: ApplicationData) -> Dict[str, Any]:
    """Сохранить данные заявления в состояние FSM"""
    return {
        "number": app.number,
        "date": app.date.isoformat() if app.date else None,
        "date_text": app.date_text,
        "applicant": app.applicant,
        "cadnum": app.cadnum,
        "purpose": app.purpose,
        "service_date": app.service_date.isoformat() if app.service_date else None,
    }


def _egrn_to_state(egrn: EGRNData) -> Dict[str, Any]:
    """Сохранить данные ЕГРН в состояние FSM"""
    # Преобразуем координаты в простые словари
    coords_dicts = []
    for c in egrn.coordinates:
        coords_dicts.append({
            'num': c.num,
            'x': c.x,
            'y': c.y
        })
    
    return {
        "cadnum": egrn.cadnum,
        "address": egrn.address,
        "area": egrn.area,
        "region": egrn.region,
        "municipality": egrn.municipality,
        "settlement": egrn.settlement,
        "permitted_use": egrn.permitted_use,
        "has_coords": egrn.has_coords,
        "capital_objects": egrn.capital_objects,
        "coordinates": coords_dicts,
    }


# ----------------------------- ТОЧКА ВХОДА ----------------------------- #
@gp_router.message(F.text == "4. Создать ГП")
async def gp_entry(m: Message, state: FSMContext):
    """Старт сценария создания ГП"""
    await state.clear()
    await state.set_state(GPStates.WAIT_APPLICATION)
    
    await m.answer(
        "📋 *СОЗДАНИЕ ГРАДОСТРОИТЕЛЬНОГО ПЛАНА*\n\n"
        "Шаг 1 из 2. Прикрепите заявление на выдачу ГП в формате *.docx*.\n\n"
        "Я извлеку данные заявителя, кадастровый номер и прочую информацию.",
        parse_mode="Markdown",
    )


# ------------------------ ШАГ 1: ЗАЯВЛЕНИЕ ------------------------ #
@gp_router.message(GPStates.WAIT_APPLICATION, F.document)
async def gp_got_application(m: Message, state: FSMContext):
    """Принимаем заявление, парсим и переходим к ЕГРН"""
    doc: TgDocument = m.document
    
    # Проверка расширения
    if not (doc.file_name and doc.file_name.lower().endswith(".docx")):
        await m.answer(
            "⚠️ Это не документ .docx\n\n"
            "Пожалуйста, прикрепите заявление в формате *.docx*.",
            parse_mode="Markdown",
        )
        return
    
    # Скачивание
    try:
        file = await m.bot.get_file(doc.file_id)
        doc_bytes = await download_with_retries(m.bot, file.file_path)
        logger.info("ГП: получен файл заявления: %s (%d байт)", doc.file_name, len(doc_bytes))
    except Exception as ex:
        logger.exception("ГП: ошибка скачивания заявления: %s", ex)
        await m.answer("❌ Не удалось скачать файл заявления. Попробуйте ещё раз.")
        return
    
    # Парсинг
    try:
        app_data: ApplicationData = parse_application_docx(doc_bytes)
        logger.info("ГП: заявление распарсено: заявитель=%s, КН=%s", app_data.applicant, app_data.cadnum)
    except Exception as ex:
        logger.exception("ГП: ошибка парсинга заявления: %s", ex)
        await m.answer(
            "❌ Не удалось разобрать заявление.\n\n"
            "Убедитесь, что файл является документом заявления в формате .docx."
        )
        return
    
    # Сохраняем
    await state.update_data(
        application_file_name=doc.file_name,
        application_data=_application_to_state(app_data),
    )
    
    # Переход к ЕГРН
    await state.set_state(GPStates.WAIT_EGRN)
    
    await m.answer(
        "✅ Заявление получено и обработано.\n\n"
        "Шаг 2 из 2. Прикрепите выписку из ЕГРН на земельный участок "
        "в формате *.xml* или *.zip*.",
        parse_mode="Markdown",
    )


@gp_router.message(GPStates.WAIT_APPLICATION)
async def gp_waiting_application_fallback(m: Message, state: FSMContext):
    """Fallback для состояния ожидания заявления"""
    await m.answer(
        "⏳ Сейчас я жду файл заявления в формате *.docx*.\n\n"
        "Пожалуйста, прикрепите документ.",
        parse_mode="Markdown",
    )


# ------------------------ ШАГ 2: ВЫПИСКА ЕГРН ------------------------ #
@gp_router.message(GPStates.WAIT_EGRN, F.document)
async def gp_got_egrn(m: Message, state: FSMContext):
    """Принимаем ЕГРН, парсим, выполняем анализ, показываем результаты"""
    doc: TgDocument = m.document
    
    # Проверка расширения
    if not doc.file_name or not (
        doc.file_name.lower().endswith(".xml")
        or doc.file_name.lower().endswith(".zip")
    ):
        await m.answer(
            "⚠️ Это не XML/ZIP-файл.\n\n"
            "Пожалуйста, прикрепите выписку из ЕГРН в формате *.xml* или *.zip*.",
            parse_mode="Markdown",
        )
        return
    
    # Скачивание
    try:
        file = await m.bot.get_file(doc.file_id)
        egrn_bytes = await download_with_retries(m.bot, file.file_path)
        logger.info("ГП: получен файл ЕГРН: %s (%d байт)", doc.file_name, len(egrn_bytes))
    except Exception as ex:
        logger.exception("ГП: ошибка скачивания ЕГРН: %s", ex)
        await m.answer("❌ Не удалось скачать файл выписки. Попробуйте ещё раз.")
        return
    
    # Парсинг
    try:
        egrn: EGRNData = parse_egrn_xml(egrn_bytes)
        logger.info("ГП: выписка распарсена: КН=%s, адрес=%s", egrn.cadnum, egrn.address)
    except Exception as ex:
        logger.exception("ГП: ошибка парсинга ЕГРН: %s", ex)
        await m.answer(
            f"❌ Не удалось разобрать выписку ЕГРН.\n\n"
            f"Ошибка: {ex}\n\n"
            "Проверьте, что приложен корректный XML/ZIP-файл выписки."
        )
        return
    
    # Проверка типа объекта
    if not egrn.is_land:
        await m.answer(
            "⚠️ Это не выписка ЕГРН по земельному участку.\n\n"
            "Пожалуйста, прикрепите выписку именно на земельный участок."
        )
        return
    
    # Сохраняем ЕГРН
    await state.update_data(
        egrn_file_name=doc.file_name,
        egrn_data=_egrn_to_state(egrn),
    )
    
    await state.set_state(GPStates.ANALYZING)
    
    # Уведомляем о начале анализа
    analyzing_msg = await m.answer(
        "🔍 *Выполняю пространственный анализ...*\n\n"
        "Это может занять несколько секунд.\n"
        "Определяю зону, ищу объекты, проверяю ограничения...",
        parse_mode="Markdown",
    )
    
    # === ВЫПОЛНЯЕМ ПРОСТРАНСТВЕННЫЙ АНАЛИЗ === #
    
    data = await state.get_data()
    app_dict = data.get("application_data", {})
    egrn_dict = data.get("egrn_data", {})
    
    # Создаём объект GPData
    gp_data = create_gp_data_from_parsed(app_dict, egrn_dict)
    
    # Выполняем анализ
    try:
        gp_data = perform_spatial_analysis(gp_data)
        logger.info("ГП: пространственный анализ завершён")
    except Exception as ex:
        logger.exception("ГП: ошибка при анализе: %s", ex)
        await analyzing_msg.edit_text(
            "❌ Произошла ошибка при пространственном анализе.\n\n"
            f"Ошибка: {ex}\n\n"
            "Попробуйте начать заново или обратитесь к администратору."
        )
        await state.clear()
        return
    
    # Сохраняем JSON результата
    gp_json = gp_data.to_json()
    await state.update_data(gp_json=gp_json)
    
    # Формируем сводку
    summary = get_analysis_summary(gp_data)
    
    # Показываем результаты
    await state.set_state(GPStates.SHOW_RESULTS)
    
    await analyzing_msg.edit_text(
        f"✅ *Анализ завершён!*\n\n{summary}",
        parse_mode="Markdown",
        reply_markup=_actions_keyboard().as_markup(),
    )


@gp_router.message(GPStates.WAIT_EGRN)
async def gp_waiting_egrn_fallback(m: Message, state: FSMContext):
    """Fallback для состояния ожидания ЕГРН"""
    await m.answer(
        "⏳ Сейчас я жду файл выписки ЕГРН (XML или ZIP).\n\n"
        "Пожалуйста, прикрепите файл.",
    )


# ---------------------- ОБРАБОТКА ДЕЙСТВИЙ ---------------------- #
@gp_router.callback_query(GPStates.SHOW_RESULTS, F.data == "gp:generate")
async def gp_generate_handler(call: CallbackQuery, state: FSMContext):
    """
    Действие "Сформировать ГП"
    
    TODO: Здесь будет генерация документа через generator/gp_builder.py
    Пока что заглушка с сохранённым JSON
    """
    await call.answer()
    
    data = await state.get_data()
    gp_json = data.get("gp_json", "{}")
    
    # TODO: Вместо вывода JSON нужно будет:
    # 1. Загрузить шаблон ГП
    # 2. Заполнить его данными из gp_json
    # 3. Сгенерировать DOCX
    # 4. Отправить пользователю
    
    await call.message.answer(
        "🚧 *Формирование документа ГП*\n\n"
        "Функционал генерации документа находится в разработке.\n\n"
        "Сейчас все данные собраны и сохранены в JSON.\n"
        "После добавления шаблона ГП будет автоматически сформирован документ.\n\n"
        "_Данные готовы для генерации:_\n"
        f"```json\n{gp_json[:500]}...\n```",
        parse_mode="Markdown",
    )
    
    await state.clear()


@gp_router.callback_query(GPStates.SHOW_RESULTS, F.data == "gp:restart")
async def gp_restart_handler(call: CallbackQuery, state: FSMContext):
    """Начать заново"""
    await call.answer()
    await state.clear()
    await state.set_state(GPStates.WAIT_APPLICATION)
    
    await call.message.answer(
        "🔄 Начинаем заново.\n\n"
        "Шаг 1 из 2. Прикрепите заявление на выдачу ГП в формате *.docx*.",
        parse_mode="Markdown",
    )


@gp_router.callback_query(F.data == "gp:cancel")
async def gp_cancel_handler(call: CallbackQuery, state: FSMContext):
    """Отмена"""
    await call.answer()
    await state.clear()
    
    await call.message.edit_text(
        "❌ Создание градостроительного плана отменено.",
    )
    
    await call.message.answer(
        "Выберите действие:",
        reply_markup=main_menu_kb(),
    )


@gp_router.message(GPStates.SHOW_RESULTS)
async def gp_show_results_fallback(m: Message, state: FSMContext):
    """Fallback для состояния просмотра результатов"""
    await m.answer(
        "Пожалуйста, выберите действие с помощью кнопок выше.",
    )