# flows/gpzu_flow.py
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.keyboards import main_menu_kb

gpzu_router = Router()


@gpzu_router.message(F.text == "6. Старый функционал")
async def gpzu_entry(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(
        "«Старый функционал» — пока подключена только заглушка.\n"
        "Дальше сюда перенесём логику работы с ЕГРН+КПТ и формированием ГПЗУ.",
        reply_markup=main_menu_kb(),
    )
