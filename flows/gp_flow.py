# flows/gp_flow.py
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.keyboards import main_menu_kb

gp_router = Router()


@gp_router.message(F.text == "4. Создать ГП")
async def gp_entry(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(
        "«Создать ГП» — функционал пока в разработке.\n"
        "Здесь будет подготовка градплана по данным участка.",
        reply_markup=main_menu_kb(),
    )
