"""Налаштування застосунку (перевизначаються змінними середовища)."""
from __future__ import annotations

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
FRONTEND_DIR = BASE_DIR.parent / "frontend"

# --- Ціноутворення (типові значення, редагуються в інтерфейсі об'єкта) ---
DEFAULTS = {
    "overhead_pct": 15.0,      # загальновиробничі витрати, % від прямих витрат
    "profit_pct": 8.0,         # кошторисний прибуток, %
    "admin_pct": 2.0,          # адміністративні витрати, %
    "risk_pct": 0.0,           # кошти на покриття ризиків, %
    "vat_pct": 20.0,           # ПДВ, %
    "materials_included": True,  # чи враховувати матеріали у кошторисі
    "round_to": 2,
}

# --- Прайс-лист із сайту ---
# Сторінка з цінами на роботи; завантажується цілком і зберігається в базі,
# тому ключі та ліміти запитів тут не потрібні. Порожнє значення — вимкнено.
PRICE_SITE = os.environ.get("BUDSMET_PRICE_SITE", "https://www.rabotniki.ua/uk/price").strip()
SITE_REFRESH_DAYS = int(os.environ.get("BUDSMET_SITE_REFRESH_DAYS", "30"))

# --- Інтернет-аналіз цін (пошукові сервіси) ---
# Провайдер задається змінною BUDSMET_PRICE_PROVIDER: off | serpapi | brave | google | custom
PRICE_PROVIDER = os.environ.get("BUDSMET_PRICE_PROVIDER", "off").strip().lower()
SERPAPI_KEY = os.environ.get("SERPAPI_KEY", "")
BRAVE_API_KEY = os.environ.get("BRAVE_API_KEY", "")
GOOGLE_CSE_KEY = os.environ.get("GOOGLE_CSE_KEY", "")
GOOGLE_CSE_CX = os.environ.get("GOOGLE_CSE_CX", "")
CUSTOM_PRICE_ENDPOINT = os.environ.get("BUDSMET_PRICE_ENDPOINT", "")
WEB_CACHE_DAYS = int(os.environ.get("BUDSMET_WEB_CACHE_DAYS", "14"))
# Скільки живих запитів до пошуку дозволено на одне перерахування кошторису.
# Безкоштовні тарифи пошукових сервісів вимірюються сотнями запитів на місяць,
# тому без обмеження одне перерахування великого об'єкта вичерпало б їх одразу.
# Відповіді з кешу не рахуються. 0 — без обмеження.
WEB_MAX_QUERIES = int(os.environ.get("BUDSMET_WEB_MAX_QUERIES", "40"))
WEB_TIMEOUT = float(os.environ.get("BUDSMET_WEB_TIMEOUT", "12"))

# Скільки останніх записів історії враховувати при усередненні ціни.
HISTORY_WINDOW = int(os.environ.get("BUDSMET_HISTORY_WINDOW", "8"))
