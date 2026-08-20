"""Прайс-лист із сайту як джерело цін.

На відміну від пошукових сервісів, тут не потрібні ключі й немає ліміту
запитів: сторінка прайса завантажується цілком, розбирається на позиції
«робота — одиниця виміру — ціна» і зберігається в базі. Далі ціни беруться
локально, а сайт перечитується раз на кілька тижнів.

Розбір навмисно не спирається на конкретні класи чи структуру сайту: беруться
всі рядки таблиць і списків, і в кожному шукається трійка «назва + одиниця +
ціна». Так парсер переживає перемальовування сайту.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

from .. import config, db
from .matcher import WorkMatcher, work_key
from .normalize import clean_text, normalize_unit, parse_number

# Одиниці виміру, які трапляються у прайсах ремонтних робіт.
_UNIT_WORDS = {
    "м2", "м²", "кв.м", "кв. м", "м.кв", "м3", "м³", "куб.м", "м", "м.п", "мп",
    "пог.м", "пог. м", "шт", "шт.", "штука", "точка", "точок", "компл", "к-т",
    "комплект", "т", "кг", "л", "год", "година", "день", "місце", "секція",
    "панель", "лист", "рулон", "блок", "отвір", "стик", "вікно", "двері",
}
_UNITS_NORM = {normalize_unit(u) for u in _UNIT_WORDS} - {""}

# «від 150 грн», «150-200 грн», «150 ₴», «150.00»
_PRICE_RE = re.compile(
    r"(?:від\s*)?(?P<a>\d{1,3}(?:[\s ]\d{3})*(?:[.,]\d+)?)"
    r"(?:\s*[-–—]\s*(?P<b>\d{1,3}(?:[\s ]\d{3})*(?:[.,]\d+)?))?"
    r"\s*(?:грн|₴|uah)?",
    re.I,
)
_HAS_DIGIT_RE = re.compile(r"\d")
# Рядок виду «Штукатурення стін   м2   150 грн» одним шматком тексту.
_INLINE_RE = re.compile(
    r"^(?P<name>.+?)[\s ]+(?P<unit>[^\s ]{1,7})[\s ]+"
    r"(?:від\s*)?(?P<price>\d{1,3}(?:[\s ]\d{3})*(?:[.,]\d+)?)\s*(?:грн|₴)?\.?$",
    re.I,
)


@dataclass
class SitePrice:
    name: str
    unit: str
    price: float
    category: str = ""
    url: str = ""


class _RowExtractor(HTMLParser):
    """Збирає рядки таблиць/списків як набори текстових комірок.

    Також запам'ятовує посилання, схожі на розділи прайса, і поточний
    заголовок — він стає категорією для позицій під ним.
    """

    _CELL_TAGS = {"td", "th"}
    _ROW_TAGS = {"tr", "li"}
    _HEADING_TAGS = {"h1", "h2", "h3", "h4", "h5"}
    _SKIP_TAGS = {"script", "style", "noscript"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[list[str], str]] = []   # (комірки, категорія)
        self.links: list[str] = []
        self._cells: list[str] = []
        self._buffer: list[str] = []
        self._in_row = False
        self._in_cell = False
        self._in_heading = False
        self._heading: list[str] = []
        self._category = ""
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)
        if tag in self._ROW_TAGS:
            self._flush_row()
            self._in_row = True
            self._cells = []
            self._buffer = []
        elif tag in self._CELL_TAGS:
            self._flush_cell()
            self._in_cell = True
        elif tag in self._HEADING_TAGS:
            self._in_heading = True
            self._heading = []
        elif tag == "br":
            self._buffer.append(" ")

    def handle_endtag(self, tag):
        if tag in self._SKIP_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag in self._CELL_TAGS:
            self._flush_cell()
            self._in_cell = False
        elif tag in self._ROW_TAGS:
            self._flush_row()
            self._in_row = False
        elif tag in self._HEADING_TAGS:
            heading = clean_text(" ".join(self._heading))
            if heading:
                self._category = heading
            self._in_heading = False
            self._heading = []

    def handle_data(self, data):
        if self._skip_depth:
            return
        if self._in_heading:
            self._heading.append(data)
        if self._in_row:
            self._buffer.append(data)

    def _flush_cell(self) -> None:
        text = clean_text(" ".join(self._buffer))
        self._buffer = []
        if text:
            self._cells.append(text)

    def _flush_row(self) -> None:
        self._flush_cell()
        if self._cells:
            self.rows.append((self._cells, self._category))
        self._cells = []


def _price_from(text: str) -> float | None:
    """Ціна з комірки; для діапазону «150-200» береться середина."""
    text = clean_text(text)
    if not _HAS_DIGIT_RE.search(text):
        return None
    match = _PRICE_RE.search(text)
    if match is None:
        return None
    low = parse_number(match.group("a"))
    high = parse_number(match.group("b")) if match.group("b") else None
    if low is None or not (1 <= low <= 5_000_000):
        return None
    if high is not None and low <= high <= 5_000_000:
        return round((low + high) / 2, 2)
    return round(low, 2)


def _unit_from(text: str) -> str:
    unit = normalize_unit(clean_text(text))
    return unit if unit in _UNITS_NORM else ""


def parse_rows(rows: list[tuple[list[str], str]], url: str = "") -> list[SitePrice]:
    """Витягує позиції прайса з рядків довільної структури."""
    found: list[SitePrice] = []
    for cells, category in rows:
        if not cells:
            continue

        # Варіант 1: назва, одиниця та ціна лежать у різних комірках.
        price, price_idx = None, -1
        for idx in range(len(cells) - 1, -1, -1):
            value = _price_from(cells[idx])
            if value is not None:
                price, price_idx = value, idx
                break
        if price is not None:
            unit, unit_idx = "", -1
            for idx, cell in enumerate(cells):
                if idx == price_idx:
                    continue
                candidate = _unit_from(cell)
                if candidate:
                    unit, unit_idx = candidate, idx
                    break
            names = [c for i, c in enumerate(cells) if i not in (price_idx, unit_idx)]
            name = max(names, key=len) if names else ""
            if len(name) >= 6 and not _looks_like_noise(name):
                found.append(SitePrice(clean_text(name), unit, price,
                                       clean_text(category), url))
                continue

        # Варіант 2: усе одним рядком тексту.
        joined = clean_text(" ".join(cells))
        match = _INLINE_RE.match(joined)
        if match:
            unit = _unit_from(match.group("unit"))
            value = _price_from(match.group("price"))
            name = clean_text(match.group("name"))
            if unit and value is not None and len(name) >= 6 and not _looks_like_noise(name):
                found.append(SitePrice(name, unit, value, clean_text(category), url))
    return found


_NOISE_RE = re.compile(
    r"^(ціна|вартість|назва|найменування|послуга|роботи|од\.?|одиниц|разом|"
    r"всього|телефон|адреса|copyright|©|меню|головна)\b", re.I)


def _looks_like_noise(name: str) -> bool:
    """Відсіює заголовки таблиці та навігацію, що потрапляють у рядки."""
    if _NOISE_RE.match(name.strip()):
        return True
    letters = sum(ch.isalpha() for ch in name)
    return letters < 5


# ------------------------------------------------------------------ завантаження

def _fetch(url: str) -> str:
    import httpx

    # Заголовки HTTP передаються в latin-1, тому лише ASCII: кирилиця тут
    # призводить до UnicodeEncodeError ще до відправлення запиту.
    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; Budsmet/1.0; construction estimate tool)",
        "Accept-Language": "uk,en;q=0.8",
        "Accept": "text/html,application/xhtml+xml",
    }
    with httpx.Client(timeout=config.WEB_TIMEOUT, follow_redirects=True, headers=headers) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def _same_site_price_links(base_url: str, links: list[str], limit: int) -> list[str]:
    """Підсторінки прайса того самого сайту (розділи робіт)."""
    base_host = urlparse(base_url).netloc
    base_path = urlparse(base_url).path.rstrip("/")
    seen, out = {base_url}, []
    for href in links:
        absolute = urljoin(base_url, href.split("#")[0])
        parsed = urlparse(absolute)
        if parsed.netloc != base_host or absolute in seen:
            continue
        if base_path and base_path in parsed.path and parsed.path.rstrip("/") != base_path:
            seen.add(absolute)
            out.append(absolute)
            if len(out) >= limit:
                break
    return out


def fetch_site(url: str, follow_sections: bool = True,
               max_pages: int = 20) -> tuple[list[SitePrice], list[str]]:
    """Завантажує прайс сайту (з розділами) і повертає позиції та перелік сторінок."""
    html = _fetch(url)
    parser = _RowExtractor()
    parser.feed(html)
    prices = parse_rows(parser.rows, url)
    pages = [url]

    if follow_sections and max_pages > 1:
        for link in _same_site_price_links(url, parser.links, max_pages - 1):
            try:
                sub_parser = _RowExtractor()
                sub_parser.feed(_fetch(link))
                sub_prices = parse_rows(sub_parser.rows, link)
            except Exception:
                continue
            if sub_prices:
                prices.extend(sub_prices)
                pages.append(link)
            time.sleep(0.5)   # не навантажуємо чужий сайт
    return _dedupe(prices), pages


def _dedupe(prices: list[SitePrice]) -> list[SitePrice]:
    """Однакові роботи з різних сторінок — лишаємо першу траплену."""
    seen, out = set(), []
    for price in prices:
        key = (work_key(price.name, price.unit), round(price.price, 2))
        if key in seen:
            continue
        seen.add(key)
        out.append(price)
    return out


# ----------------------------------------------------------------------- сховище

def save(url: str, prices: list[SitePrice]) -> int:
    with db.transaction() as conn:
        conn.execute("DELETE FROM site_prices WHERE site = ?", (url,))
        conn.executemany(
            """INSERT INTO site_prices(site, name, unit, price, category, url, work_key)
               VALUES (?,?,?,?,?,?,?)""",
            [(url, p.name, p.unit, p.price, p.category, p.url, work_key(p.name, p.unit))
             for p in prices])
    return len(prices)


def stored(url: str | None = None) -> list[dict]:
    site = url or config.PRICE_SITE
    return [dict(r) for r in db.query(
        "SELECT * FROM site_prices WHERE site = ? ORDER BY id", (site,))]


def age_days(url: str | None = None) -> float | None:
    site = url or config.PRICE_SITE
    row = db.query_one(
        """SELECT (julianday('now') - julianday(MIN(fetched_at))) AS age
             FROM site_prices WHERE site = ?""", (site,))
    return None if row is None or row["age"] is None else float(row["age"])


def refresh(url: str | None = None, force: bool = False) -> dict:
    """Перечитує прайс сайту, якщо він застарів або якщо force."""
    site = url or config.PRICE_SITE
    if not site:
        return {"site": "", "saved": 0, "error": "адресу прайса не задано"}
    current_age = age_days(site)
    if not force and current_age is not None and current_age < config.SITE_REFRESH_DAYS:
        return {"site": site, "saved": len(stored(site)), "age_days": round(current_age, 1),
                "refreshed": False}
    try:
        prices, pages = fetch_site(site)
    except Exception as exc:
        _FAILED_AT[site] = time.time()
        return {"site": site, "saved": len(stored(site)), "refreshed": False,
                "error": f"{type(exc).__name__}: {exc}"[:200]}
    if not prices:
        _FAILED_AT[site] = time.time()
        return {"site": site, "saved": 0, "refreshed": False,
                "error": "на сторінці не знайдено позицій «робота — одиниця — ціна»"}
    _FAILED_AT.pop(site, None)
    save(site, prices)
    _MATCHER_CACHE.pop(site, None)
    return {"site": site, "saved": len(prices), "pages": len(pages), "refreshed": True}


# ------------------------------------------------------------------- підбір ціни

_MATCHER_CACHE: dict[str, tuple[WorkMatcher, list[dict]]] = {}
# Після невдалої спроби завантаження не ломимось у сайт знову: інакше
# кошторис на сотню позицій зробив би сотню запитів по таймауту кожен.
_FAILED_AT: dict[str, float] = {}
FAILED_RETRY_SECONDS = 900


def _recently_failed(site: str) -> bool:
    """Чи була нещодавно невдала спроба завантажити цей прайс."""
    failed_at = _FAILED_AT.get(site)
    return failed_at is not None and (time.time() - failed_at) < FAILED_RETRY_SECONDS


def _matcher(site: str):
    cached = _MATCHER_CACHE.get(site)
    if cached is None:
        rows = stored(site)
        if not rows:
            return None
        items = [{"code": str(r["id"]), "name": r["name"], "unit": r["unit"],
                  "category": r["category"], "labor": r["price"], "material": 0.0,
                  "machines": 0.0} for r in rows]
        cached = (WorkMatcher(items), items)
        _MATCHER_CACHE[site] = cached
    return cached[0]


def lookup(name: str, unit: str, auto_refresh: bool = True) -> dict | None:
    """Ціна роботи з прайса сайту або None, якщо збігу немає."""
    site = config.PRICE_SITE
    if not site:
        return None
    if auto_refresh and not _recently_failed(site):
        age = age_days(site)
        if age is None or age >= config.SITE_REFRESH_DAYS:
            refresh(site)
    matcher = _matcher(site)
    if matcher is None:
        return None
    best = matcher.best(name, unit)
    if best is None:
        return None
    item = best.item
    return {
        "labor": round(float(item.get("labor", 0)), 2),
        "material": 0.0,
        "machines": 0.0,
        "matched_name": item["name"],
        "matched_unit": item.get("unit", ""),
        "category": item.get("category", ""),
        "score": best.score,
        "site": site,
    }


def invalidate() -> None:
    _MATCHER_CACHE.clear()
    _FAILED_AT.clear()
