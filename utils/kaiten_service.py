# utils/kaiten_service.py
import aiohttp
import logging
from typing import Optional, Dict, Any

from core.config import KAITEN_API_TOKEN, KAITEN_DOMAIN, KAITEN_BOARD_ID, KAITEN_COLUMN_ID

logger = logging.getLogger("gpzu-bot.kaiten_service")

BASE_URL = f"https://{KAITEN_DOMAIN}/api/latest"

headers = {
    "Authorization": f"Bearer {KAITEN_API_TOKEN}",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


async def create_card(
    title: str,
    description: str,
    due_date: Optional[str] = None,
    board_id: int = KAITEN_BOARD_ID,
    column_id: int = KAITEN_COLUMN_ID,
    properties: Optional[Dict[str, Any]] = None,
) -> Optional[int]:
    """
    Создает карточку в Kaiten, используя snake_case для полей API.
    При необходимости заполняет кастомные поля через properties.
    """
    if not board_id:
        logger.error("KAITEN_BOARD_ID не настроен")
        return None

    url = f"{BASE_URL}/cards"

    payload: Dict[str, Any] = {
        "board_id": board_id,
        "column_id": column_id,
        "title": title,
        "description": description,
    }

    if due_date:
        payload["due_date"] = due_date

    if properties:
        payload["properties"] = properties

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, json=payload, headers=headers) as resp:
                if resp.status in (200, 201):
                    data = await resp.json()
                    card_id = data.get("id")
                    logger.info(
                        f"Карточка создана: ID {card_id} "
                        f"(Board: {board_id}, Column: {column_id})"
                    )
                    return card_id
                else:
                    error_text = await resp.text()
                    logger.error(
                        f"Ошибка создания карточки Kaiten: {resp.status} - {error_text}"
                    )
                    return None
        except Exception as e:
            logger.exception(f"Исключение при создании карточки: {e}")
            return None


async def upload_attachment(card_id: int, file_name: str, file_data: bytes) -> bool:
    """
    Загружает файл в созданную карточку.
    """
    url = f"{BASE_URL}/cards/{card_id}/attachments"

    upload_headers = {
        "Authorization": f"Bearer {KAITEN_API_TOKEN}",
        "Accept": "application/json",
    }

    form = aiohttp.FormData()
    form.add_field("file", file_data, filename=file_name)

    async with aiohttp.ClientSession() as session:
        try:
            async with session.post(url, data=form, headers=upload_headers) as resp:
                if resp.status in (200, 201):
                    logger.info(
                        f"Файл {file_name} успешно загружен в карточку {card_id}"
                    )
                    return True
                else:
                    text = await resp.text()
                    logger.error(
                        f"Ошибка загрузки файла {file_name}: {resp.status} - {text}"
                    )
                    return False
        except Exception as e:
            logger.exception(f"Исключение при загрузке файла: {e}")
            return False
