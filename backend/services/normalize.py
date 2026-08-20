"""Нормалізація українських найменувань робіт та одиниць виміру.

Використовується і матчером (пошук розцінки в довіднику), і імпортером
(розбір відомості обсягів робіт), тому тримається без зовнішніх залежностей.
"""
from __future__ import annotations

import re
import unicodedata

# Різновиди апострофа, які трапляються у вигрузках АВК-5 / Word / 1С.
_APOSTROPHES = "’‘ʼ`´'"

# Слова, що не несуть змісту для співставлення розцінок.
_STOPWORDS = {
    "з", "із", "зі", "та", "і", "й", "в", "у", "на", "до", "для", "при",
    "або", "по", "з'", "як", "що", "а", "не", "від", "під", "над", "о",
    "тощо", "т", "п", "тип", "типу", "шт", "мм", "см", "м", "кг",
}

# Канонічні одиниці виміру: усе, що зліва, зводиться до значення справа.
_UNIT_ALIASES = {
    # Ключі «злитих» форм (без пробілів і крапок) потрібні для варіантів на
    # кшталт «кв. м» — вони зводяться до compact-вигляду перед пошуком.
    "м2": "м2", "м²": "м2", "кв.м": "м2", "кв м": "м2", "м.кв": "м2",
    "м кв": "м2", "m2": "м2", "мкв": "м2", "квм": "м2", "кв": "м2",
    "м3": "м3", "м³": "м3", "куб.м": "м3", "куб м": "м3", "м.куб": "м3",
    "m3": "м3", "мкуб": "м3", "кубм": "м3", "куб": "м3",
    "м": "м", "мп": "м", "м.п": "м", "пог.м": "м", "п.м": "м", "м п": "м",
    "погм": "м", "пм": "м", "пог м": "м", "погонний метр": "м",
    "шт": "шт", "штук": "шт", "штуки": "шт", "од": "шт", "одиниць": "шт",
    "т": "т", "тонн": "т", "тонна": "т",
    "кг": "кг",
    "к-т": "компл", "кт": "компл", "комплект": "компл", "компл": "компл",
    "комплектів": "компл", "к т": "компл",
    "блок": "блок", "блоків": "блок",
    "грати": "грати", "решітка": "грати",
    "агрегат": "агрегат", "агрегатів": "агрегат",
    "квт": "кВт", "kw": "кВт",
    "кінців": "кінців", "кінець": "кінців",
    "лінія": "лінія", "ліній": "лінія",
    "вимір": "вимір", "вимірюв": "вимір", "вимірювання": "вимір",
    "випроб": "випроб", "випробування": "випроб",
    "точ": "точ", "точка": "точ", "точок": "точ",
    "струм-ч": "струм-ч", "струм ч": "струм-ч",
    "фазув-ня": "фазув-ня", "фазування": "фазув-ня", "фазув ня": "фазув-ня",
    "маш-год": "маш-год", "люд-год": "люд-год", "чол-год": "люд-год",
    "місце": "місце", "об'єкт": "об'єкт", "система": "система",
    "%": "%",
}

_NUM_RE = re.compile(r"^-?\d{1,3}(?:[  ]\d{3})*(?:[.,]\d+)?$|^-?\d+(?:[.,]\d+)?$")


def fix_apostrophes(text: str) -> str:
    """Зводить усі варіанти апострофа до звичайного `'`."""
    for ch in _APOSTROPHES:
        text = text.replace(ch, "'")
    return text


def soft_clean(text: str) -> str:
    """Мінімальне чищення: зберігає подвійні пробіли (вони розділяють колонки в PDF)."""
    if text is None:
        return ""
    text = unicodedata.normalize("NFC", str(text))
    text = fix_apostrophes(text)
    text = text.replace("\t", " ")
    text = re.sub(r"[\u00a0\u2007\u202f\u2009]", " ", text)
    return text.rstrip()


def clean_text(text: str) -> str:
    """Прибирає керуючі символи, зайві пробіли та переноси, лишає читабельний текст."""
    if text is None:
        return ""
    text = unicodedata.normalize("NFC", str(text))
    text = fix_apostrophes(text)
    # АВК-5 переносить довгі назви — склеєні через дефіс частини слова зшиваємо.
    text = re.sub(r"(?<=\w)-\s*\n\s*(?=\w)", "", text)
    text = text.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    text = re.sub(r"[   ]", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip()


def normalize_name(text: str) -> str:
    """Ключ для співставлення найменувань: без пунктуації, стоп-слів та цифр-дрібниць."""
    text = clean_text(text).lower()
    # `Роздiл` в АВК пишеться з латинською `i` — приводимо латиницю-омоглифи до кирилиці.
    for lat, cyr in (("i", "і"), ("a", "а"), ("e", "е"), ("o", "о"),
                     ("p", "р"), ("c", "с"), ("x", "х"), ("y", "у")):
        text = text.replace(lat, cyr)
    text = re.sub(r"[\[\](){}«»\"“”]", " ", text)
    text = re.sub(r"[/\\,;:.!?–—_+*]", " ", text)
    text = re.sub(r"\s{2,}", " ", text)
    tokens = [t for t in text.split(" ") if t and t not in _STOPWORDS]
    return " ".join(tokens).strip()


def stem(token: str) -> str:
    """Груба нормалізація закінчення слова — достатньо для співставлення розцінок."""
    for suffix in ("ювання", "ування", "ання", "ення", "ості", "ими", "ого", "ому",
                   "их", "ах", "ям", "ями", "ів", "ей", "ою", "ій", "ий", "ая",
                   "ої", "ом", "ем", "у", "ю", "а", "я", "и", "і", "е", "о"):
        if len(token) - len(suffix) >= 4 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def stem_phrase(text: str) -> str:
    """Нормалізований рядок зі стемованими токенами — основа fuzzy-порівняння."""
    return " ".join(stem(t) for t in normalize_name(text).split())


def normalize_unit(unit: str) -> str:
    """Канонічна одиниця виміру ('кв.м' → 'м2', 'к-т' → 'компл')."""
    if not unit:
        return ""
    raw = clean_text(unit).lower().rstrip(".").strip()
    raw = raw.replace("²", "2").replace("³", "3")
    if raw in _UNIT_ALIASES:
        return _UNIT_ALIASES[raw]
    compact = re.sub(r"[\s.]", "", raw)
    if compact in _UNIT_ALIASES:
        return _UNIT_ALIASES[compact]
    return raw


def parse_number(value) -> float | None:
    """Розбирає число у форматі '1 234,56' / '1234.56' / '7,36'."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    raw = clean_text(value).replace(" ", "")
    if not raw or not _NUM_RE.match(raw.replace(" ", "")):
        raw2 = raw.replace(",", ".")
        try:
            return float(raw2)
        except ValueError:
            return None
    return float(raw.replace(",", "."))


def looks_like_number(value: str) -> bool:
    return parse_number(value) is not None
