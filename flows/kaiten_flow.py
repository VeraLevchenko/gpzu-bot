# flows/kaiten_flow.py
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.keyboards import main_menu_kb

kaiten_router = Router()


@kaiten_router.message(F.text == "1. Создать задачу Кайтен")
async def kaiten_entry(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(
        "«Создать задачу Кайтен» — функционал пока в разработке.\n"
        "Здесь позже будет создание задач в Кайтен по данным участка/документов.",
        reply_markup=main_menu_kb(),
    )
