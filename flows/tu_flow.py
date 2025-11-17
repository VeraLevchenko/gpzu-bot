# flows/tu_flow.py
import os
import tempfile
import logging
from typing import Optional, Dict, Any, List, Tuple

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
    kb.button(text="Далее", callback_data="tu:next")
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


# ------------------------ ХРАНЕНИЕ EGRN В FSM ------------------------ #
def _egrn_to_state(e: EGRNData) -> Dict[str, Any]:
    """
    Сохраняем только те поля, которые реально используются в шаблонах ТУ.
    Остальные в EGRNData имеют значения по умолчанию.
    """
    return {
        "cadnum": e.cadnum,
        "address": e.address,
        "area": e.area,
        "permitted_use": getattr(e, "permitted_use", None),
    }


def _egrn_from_state(d: Dict[str, Any]) -> EGRNData:
    """
    Восстанавливаем EGRNData из dict.
    Благодаря значениям по умолчанию в dataclass можно передавать только часть полей.
    """
    return EGRNData(
        cadnum=d.get("cadnum"),
        address=d.get("address"),
        area=d.get("area"),
        permitted_use=d.get("permitted_use"),
    )


# ------------------------------ ВХОД В СЦЕНАРИЙ ------------------------------ #
@tu_router.message(F.text == "3. Подготовить запросы ТУ")
async def tu_entry(m: Message, state: FSMContext):
    """
    Старт сценария подготовки запросов ТУ.
    """
    await state.clear()
    await state.set_state(TUStates.WAIT_INCOMING)

    await m.answer(
        "Подготовка запросов ТУ.\n\n"
        "Шаг 1. Введите входящий номер заявления и входящую дату в формате:\n"
        "124245 от 01.11.2025\n\n"
        "После ввода я попрошу вас подтвердить данные и перейти к загрузке выписки ЕГРН.",
    )


# -------------------------- ШАГ 1: ВХОДЯЩИЙ -------------------------- #
@tu_router.message(TUStates.WAIT_INCOMING, F.text)
async def tu_got_incoming(m: Message, state: FSMContext):
    """
    Пользователь вводит входящий номер и дату в свободной форме.
    Мы сохраняем строку как есть.
    """
    incoming_raw = (m.text or "").strip()
    if not incoming_raw:
        await m.answer(
            "Не удалось распознать текст входящего.\n"
            "Пожалуйста, введите строку в формате, например:\n"
            "124245 от 01.11.2025"
        )
        return

    await state.update_data(incoming=incoming_raw)

    await m.answer(
        f"Приняла:\n{incoming_raw}\n\n"
        f"Если всё верно, нажмите «Далее».",
        reply_markup=_next_button().as_markup(),
    )


@tu_router.callback_query(TUStates.WAIT_INCOMING, F.data == "tu:next")
async def tu_after_incoming(call: CallbackQuery, state: FSMContext):
    """
    Пользователь подтвердил входящие данные — переходим к шагу загрузки выписки.
    """
    await call.answer()
    await state.set_state(TUStates.WAIT_EGRN)

    await call.message.answer(
        "Шаг 2. Прикрепите выписку из ЕГРН на земельный участок "
        "в формате *.xml* или *.zip* (файл выписки).\n\n"
        "После загрузки файла я покажу данные по участку и предложу варианты подготовки ТУ.",
        parse_mode="Markdown",
    )


@tu_router.message(TUStates.WAIT_INCOMING)
async def tu_waiting_incoming_fallback(m: Message, state: FSMContext):
    """
    Любые другие сообщения в состоянии WAIT_INCOMING.
    """
    await m.answer(
        "Сейчас я жду строку с входящим номером и датой, например:\n"
        "124245 от 01.11.2025"
    )


# -------------------------- ШАГ 2: ВЫПИСКА ЕГРН -------------------------- #
@tu_router.message(TUStates.WAIT_EGRN, F.document)
async def tu_got_egrn(m: Message, state: FSMContext):
    """
    Принимаем файл выписки (xml или zip), скачиваем, парсим,
    проверяем, что это ЗУ, показываем данные и предлагаем выбрать режим подготовки ТУ.
    """
    doc: TgDocument = m.document

    if not doc.file_name or not (
        doc.file_name.lower().endswith(".xml")
        or doc.file_name.lower().endswith(".zip")
    ):
        await m.answer(
            "Это не XML/ZIP-файл.\n"
            "Пожалуйста, пришлите выписку из ЕГРН на земельный участок "
            "в формате *.xml* или *.zip*.",
        )
        return

    data = await state.get_data()
    incoming = data.get("incoming") or ""

    # 1. Скачиваем
    try:
        file = await m.bot.get_file(doc.file_id)
        egrn_bytes = await download_with_retries(m.bot, file.file_path)
        logger.info(
            "TU: получен файл ЕГРН: %s (%d байт), входящий=%s",
            doc.file_name,
            len(egrn_bytes),
            incoming,
        )
    except Exception as ex:
        logger.exception("TU: ошибка скачивания ЕГРН: %s", ex)
        await m.answer(
            f"Не удалось скачать файл выписки: {ex}\n"
            "Попробуйте отправить файл ещё раз."
        )
        return

    # 2. Парсим
    try:
        egrn: EGRNData = parse_egrn_xml(egrn_bytes)
    except Exception as ex:
        logger.exception("TU: ошибка парсинга ЕГРН: %s", ex)
        await m.answer(
            f"Не удалось разобрать выписку ЕГРН: {ex}\n"
            "Проверьте, что приложен корректный XML/ZIP-файл."
        )
        return

    # 3. Проверяем тип объекта
    if not egrn.is_land:
        await m.answer(
            "Это не выписка ЕГРН по земельному участку.\n"
            "Пожалуйста, прикрепите выписку на земельный участок."
        )
        return

    # 4. Сохраняем EGRN в состояние
    await state.update_data(egrn=_egrn_to_state(egrn))
    await state.set_state(TUStates.WAIT_ACTION)

    # 5. Показываем пользователю краткие данные по участку
    vri = getattr(egrn, "permitted_use", None)
    area_txt = egrn.area or "—"
    addr_txt = egrn.address or "—"

    text_lines: List[str] = []
    text_lines.append("Данные по земельному участку из ЕГРН:")
    text_lines.append(f"Кадастровый номер: {egrn.cadnum or '—'}")
    text_lines.append(f"Площадь: {area_txt} кв. м")
    text_lines.append(f"ВРИ: {vri or '—'}")
    text_lines.append(f"Адрес: {addr_txt}")
    text = "\n".join(text_lines)

    await m.answer(
        text + "\n\nВыберите вариант подготовки запросов ТУ:",
        reply_markup=_tu_mode_keyboard().as_markup(),
    )


@tu_router.message(TUStates.WAIT_EGRN)
async def tu_waiting_egrn_fallback(m: Message, state: FSMContext):
    """
    Любые другие сообщения в состоянии WAIT_EGRN.
    """
    await m.answer(
        "Сейчас я жду файл выписки ЕГРН (XML или ZIP).\n"
        "Пожалуйста, прикрепите файл выписки.",
    )


# ---------------------- ВАРИАНТ 1: БЕЗ ИСХОДЯЩЕГО ---------------------- #
@tu_router.callback_query(TUStates.WAIT_ACTION, F.data == "tu:without_outgoing")
async def tu_without_outgoing(call: CallbackQuery, state: FSMContext):
    """
    Вариант: подготовить ТУ для последующей самостоятельной регистрации.
    Исходящий номер и дата не заполняются (поля остаются пустыми).
    """
    await call.answer()

    data = await state.get_data()
    incoming = data.get("incoming") or ""
    egrn_state = data.get("egrn") or {}
    egrn = _egrn_from_state(egrn_state)

    await call.message.answer(
        "Готовлю запросы ТУ для последующей самостоятельной регистрации.\n"
        "Пожалуйста, подождите...",
    )

    try:
        docs: List[Tuple[str, bytes]] = build_tu_docs(egrn, incoming)
    except Exception as ex:
        logger.exception("TU: ошибка формирования ТУ без исходящего: %s", ex)
        await call.message.answer(
            f"Не удалось сформировать запросы ТУ: {ex}"
        )
        await state.clear()
        return

    # Отправляем сформированные DOCX
    for filename, file_bytes in docs:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
        try:
            tmp.write(file_bytes)
            tmp.flush()
            tmp.close()
            await call.message.answer_document(
                FSInputFile(tmp.name, filename=filename)
            )
        finally:
            try:
                os.remove(tmp.name)
            except Exception:
                pass

    await call.message.answer(
        "Запросы ТУ сформированы.\n"
        "Можете сохранить файлы и вернуться в главное меню."
    )
    await state.clear()


# ---------------------- ВАРИАНТ 2: С ИСХОДЯЩИМ ---------------------- #
@tu_router.callback_query(TUStates.WAIT_ACTION, F.data == "tu:with_outgoing")
async def tu_with_outgoing(call: CallbackQuery, state: FSMContext):
    """
    Вариант: подготовить ТУ с исходящим номером и датой.
    Номера и даты берутся/записываются в журнал запросов ТУ (Excel).
    """
    await call.answer()

    data = await state.get_data()
    incoming = data.get("incoming") or ""
    egrn_state = data.get("egrn") or {}
    egrn = _egrn_from_state(egrn_state)

    await call.message.answer(
        "Готовлю запросы ТУ с присвоением исходящего номера и даты.\n"
        "Пожалуйста, подождите...",
    )

    try:
        docs: List[Tuple[str, bytes]] = build_tu_docs_with_outgoing(egrn, incoming)
    except Exception as ex:
        logger.exception("TU: ошибка формирования ТУ с исходящим: %s", ex)
        await call.message.answer(
            f"Не удалось сформировать запросы ТУ: {ex}"
        )
        await state.clear()
        return

    # Отправляем сформированные DOCX
    for filename, file_bytes in docs:
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".docx")
        try:
            tmp.write(file_bytes)
            tmp.flush()
            tmp.close()
            await call.message.answer_document(
                FSInputFile(tmp.name, filename=filename)
            )
        finally:
            try:
                os.remove(tmp.name)
            except Exception:
                pass

    await call.message.answer(
        "Запросы ТУ сформированы.\n"
        "Можете сохранить файлы и вернуться в главное меню."
    )
    await state.clear()


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
