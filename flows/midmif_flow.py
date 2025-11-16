# flows/midmif_flow.py
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.keyboards import main_menu_kb

midmif_router = Router()


@midmif_router.message(F.text == "2. Подготовить MID/MIF")
async def midmif_entry(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(
        "«Подготовить MID/MIF» — функционал пока в разработке.\n"
        "Здесь будет формирование MID/MIF по выбранным данным из ГИС/ЕГРН.",
        reply_markup=main_menu_kb(),
    )
