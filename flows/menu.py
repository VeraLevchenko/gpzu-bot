# flows/menu.py
from aiogram import Router
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.keyboards import main_menu_kb

menu_router = Router()


@menu_router.message(CommandStart())
async def cmd_start(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(
        "Выберите действие:",
        reply_markup=main_menu_kb(),
    )
