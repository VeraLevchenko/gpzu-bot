# core/keyboards.py
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def main_menu_kb() -> ReplyKeyboardMarkup:
    """
    Постоянное главное меню.
    """
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="1. Создать задачу Кайтен")],
            [KeyboardButton(text="2. Подготовить MID/MIF")],
            [KeyboardButton(text="3. Подготовить запросы ТУ")],
            [KeyboardButton(text="4. Создать ГП")],
            [KeyboardButton(text="5. Сформировать чек-лист")],
            [KeyboardButton(text="6. Старый функционал")],
        ],
        resize_keyboard=True,
    )
