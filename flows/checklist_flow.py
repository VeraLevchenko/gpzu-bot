# flows/checklist_flow.py
from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from core.keyboards import main_menu_kb

checklist_router = Router()


@checklist_router.message(F.text == "5. Сформировать чек-лист")
async def checklist_entry(m: Message, state: FSMContext):
    await state.clear()
    await m.answer(
        "«Сформировать чек-лист» — функционал пока в разработке.\n"
        "Здесь будем собирать чек-листы по типовым процедурам.",
        reply_markup=main_menu_kb(),
    )
