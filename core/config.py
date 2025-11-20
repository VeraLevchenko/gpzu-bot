# core/config.py
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not BOT_TOKEN:
    raise RuntimeError("Put TELEGRAM_BOT_TOKEN=... into .env")

# --- KAITEN SETTINGS ---
# Токен API
KAITEN_API_TOKEN = os.getenv("KAITEN_API_TOKEN", "")

# Домен вашей компании (теперь без http(s)://)
KAITEN_DOMAIN = os.getenv("KAITEN_DOMAIN", "isogd2019.kaiten.ru")

# ID пространства (Space ID). Обновлен.
try:
    KAITEN_SPACE_ID = int(os.getenv("KAITEN_SPACE_ID", "627862"))
except ValueError:
    KAITEN_SPACE_ID = 627862

# ID доски (Board ID). Обновлен до 1426028.
try:
    KAITEN_BOARD_ID = int(os.getenv("KAITEN_BOARD_ID", "1426028"))
except ValueError:
    KAITEN_BOARD_ID = 1426028

# ID колонки (Column ID). Обновлен до 4952339.
try:
    KAITEN_COLUMN_ID = int(os.getenv("KAITEN_COLUMN_ID", "4952339"))
except ValueError:
    KAITEN_COLUMN_ID = 4952339

# Внутренние ключи полей Kaiten
KAITEN_FIELD_CADNUM = "id_238069"          # Исх_данные 1 Кадастровый номер
KAITEN_FIELD_SUBMIT_METHOD = "id_270924"   # Способ подачи

# Значение справочника для "Способ подачи" = ЕПГУ
KAITEN_SUBMIT_METHOD_EPGU = 93413

# Новое поле: "входящая дата" (дата заявления)
KAITEN_FIELD_INCOMING_DATE = "id_228500"