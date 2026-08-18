"""Аналіз цін в інтернеті по місту.

Провайдери підключаються через змінну середовища `BUDSMET_PRICE_PROVIDER`:

    off      — вимкнено (типово): ціни беруться з історії та довідника;
    serpapi  — SerpAPI (SERPAPI_KEY);
    brave    — Brave Search API (BRAVE_API_KEY);
    google   — Google Programmable Search (GOOGLE_CSE_KEY + GOOGLE_CSE_CX);
    custom   — власний HTTP-ендпоінт (BUDSMET_PRICE_ENDPOINT), який повертає
               {"results": [{"title": ..., "snippet": ..., "url": ...}]}.

Провайдер повертає текстові сніпети, з яких видобуваються ціни «грн/м2» тощо.
Результат кешується у таблиці web_price_cache на BUDSMET_WEB_CACHE_DAYS днів.
"""
from __future__ import annotations

import json
import re
import statistics
from dataclasses import dataclass, field

from .. import config, db
from .matcher import work_key
from .normalize import clean_text, normalize_unit

# «450 грн/м2», «від 1 200 грн за м.кв.», «250 грн/м²», «300 - 400 грн/шт»
_PRICE_RE = re.compile(
    r"(?P<price>\d{1,3}(?:[  ]\d{3})*(?:[.,]\d+)?)\s*"
    r"(?:грн|₴|uah)\.?\s*"
    r"(?:/|за\s+|/\s*)?\s*"
    r"(?P<unit>м2|м²|кв\.?\s?м|м\.?кв|м3|м³|куб\.?\s?м|пог\.?\s?м|м\.?п|м|шт|компл\.?|"
    r"комплект|точк[ауи]|т|кг|блок|годин[ауи])?",
    re.I,
)
_RANGE_RE = re.compile(
    r"(?P<a>\d{1,3}(?:[  ]\d{3})*(?:[.,]\d+)?)\s*[-–—]\s*(?P<b>\d{1,3}(?:[  ]\d{3})*(?:[.,]\d+)?)\s*(?:грн|₴)",
    re.I,
)
# Явні маркери того, що ціна стосується саме роботи, а не матеріалу.
_LABOR_HINTS = ("робот", "монтаж", "послуг", "укладанн", "влаштуванн", "улаштуванн",
                "демонтаж", "ціна за роботу", "вартість робіт", "майстер", "бригад")
_MATERIAL_HINTS = ("матеріал", "купити", "доставка", "в наявності", "товар", "склад")


@dataclass
class PriceSample:
    value: float
    unit: str
    kind: str            # "labor" | "material"
    title: str = ""
    url: str = ""


@dataclass
class WebPrice:
    labor: float = 0.0
    material: float = 0.0
    provider: str = ""
    samples: list = field(default_factory=list)
    cached: bool = False

    @property
    def found(self) -> bool:
        return bool(self.labor or self.material)


def _to_float(raw: str) -> float | None:
    raw = raw.replace(" ", "").replace(" ", "").replace(",", ".")
    try:
        return float(raw)
    except ValueError:
        return None


def extract_prices(text: str, want_unit: str, title: str = "", url: str = "") -> list[PriceSample]:
    """Видобуває ціни з тексту сніпета, лишаючи тільки сумісні з потрібною одиницею."""
    text = clean_text(text)
    if not text:
        return []
    low = text.lower()
    kind = "material" if (any(h in low for h in _MATERIAL_HINTS)
                          and not any(h in low for h in _LABOR_HINTS)) else "labor"
    want = normalize_unit(want_unit)
    out: list[PriceSample] = []

    for m in _RANGE_RE.finditer(text):
        a, b = _to_float(m.group("a")), _to_float(m.group("b"))
        if a and b and 0 < a <= b:
            out.append(PriceSample((a + b) / 2, want, kind, title, url))

    for m in _PRICE_RE.finditer(text):
        value = _to_float(m.group("price"))
        if value is None or not (1 <= value <= 5_000_000):
            continue
        unit = normalize_unit(m.group("unit") or "")
        if want and unit and unit != want:
            continue
        out.append(PriceSample(value, unit or want, kind, title, url))
    return out


# ------------------------------------------------------------------ провайдери

def _http_get(url: str, params: dict, headers: dict | None = None) -> dict:
    import httpx

    with httpx.Client(timeout=config.WEB_TIMEOUT, follow_redirects=True) as client:
        resp = client.get(url, params=params, headers=headers or {})
        resp.raise_for_status()
        return resp.json()


def _provider_serpapi(query: str) -> list[dict]:
    data = _http_get("https://serpapi.com/search.json",
                     {"q": query, "hl": "uk", "gl": "ua", "api_key": config.SERPAPI_KEY})
    return [{"title": r.get("title", ""), "snippet": r.get("snippet", ""), "url": r.get("link", "")}
            for r in data.get("organic_results", [])]


def _provider_brave(query: str) -> list[dict]:
    data = _http_get("https://api.search.brave.com/res/v1/web/search",
                     {"q": query, "country": "ua", "search_lang": "uk", "count": 20},
                     {"X-Subscription-Token": config.BRAVE_API_KEY, "Accept": "application/json"})
    return [{"title": r.get("title", ""), "snippet": r.get("description", ""), "url": r.get("url", "")}
            for r in data.get("web", {}).get("results", [])]


def _provider_google(query: str) -> list[dict]:
    data = _http_get("https://www.googleapis.com/customsearch/v1",
                     {"q": query, "key": config.GOOGLE_CSE_KEY, "cx": config.GOOGLE_CSE_CX,
                      "hl": "uk", "gl": "ua", "num": 10})
    return [{"title": r.get("title", ""), "snippet": r.get("snippet", ""), "url": r.get("link", "")}
            for r in data.get("items", [])]


def _provider_custom(query: str) -> list[dict]:
    data = _http_get(config.CUSTOM_PRICE_ENDPOINT, {"q": query})
    return data.get("results", [])


_PROVIDERS = {
    "serpapi": (_provider_serpapi, lambda: bool(config.SERPAPI_KEY)),
    "brave": (_provider_brave, lambda: bool(config.BRAVE_API_KEY)),
    "google": (_provider_google, lambda: bool(config.GOOGLE_CSE_KEY and config.GOOGLE_CSE_CX)),
    "custom": (_provider_custom, lambda: bool(config.CUSTOM_PRICE_ENDPOINT)),
}


def provider_status() -> dict:
    name = config.PRICE_PROVIDER
    entry = _PROVIDERS.get(name)
    return {
        "provider": name,
        "enabled": bool(entry and entry[1]()),
        "available": sorted(_PROVIDERS),
        "reason": "" if entry and entry[1]() else (
            "аналіз цін в інтернеті вимкнено (BUDSMET_PRICE_PROVIDER=off)" if name in ("", "off")
            else f"для провайдера «{name}» не задано ключ доступу"),
    }


# ------------------------------------------------------------------ кеш і запит

def _cache_get(key: str, city: str, provider: str) -> WebPrice | None:
    row = db.query_one(
        """SELECT * FROM web_price_cache
           WHERE work_key = ? AND city = ? AND provider = ?
             AND created_at > datetime('now', ?)""",
        (key, city, provider, f"-{config.WEB_CACHE_DAYS} days"))
    if row is None:
        return None
    return WebPrice(labor=row["labor_price"], material=row["material_price"],
                    provider=provider, samples=db.load_json_field(row["samples"], []), cached=True)


def _cache_put(key: str, city: str, unit: str, price: WebPrice) -> None:
    db.execute(
        """INSERT INTO web_price_cache(work_key, city, unit, labor_price, material_price, samples, provider, created_at)
           VALUES (?,?,?,?,?,?,?, datetime('now'))
           ON CONFLICT(work_key, city, provider) DO UPDATE SET
             labor_price=excluded.labor_price, material_price=excluded.material_price,
             samples=excluded.samples, unit=excluded.unit, created_at=datetime('now')""",
        (key, city, unit, price.labor, price.material, json.dumps(price.samples, ensure_ascii=False),
         price.provider))


def _aggregate(samples: list[PriceSample]) -> tuple[float, float]:
    """Медіана по роботах і матеріалах — стійка до випадкових викидів у сніпетах."""
    def median_of(kind: str) -> float:
        values = sorted(s.value for s in samples if s.kind == kind)
        if not values:
            return 0.0
        if len(values) > 4:  # відкидаємо крайні 20% з обох боків
            cut = max(1, len(values) // 5)
            values = values[cut:-cut] or values
        return round(statistics.median(values), 2)

    return median_of("labor"), median_of("material")


def lookup(name: str, unit: str, city: str = "", region: str = "",
           use_cache: bool = True) -> WebPrice:
    """Шукає ринкову ціну роботи в інтернеті по місту. Повертає порожній WebPrice, якщо вимкнено."""
    status = provider_status()
    if not status["enabled"]:
        return WebPrice(provider=status["provider"])

    key = work_key(name, unit)
    provider_name = status["provider"]
    if use_cache:
        cached = _cache_get(key, city, provider_name)
        if cached is not None:
            return cached

    place = city or region or "Україна"
    query = f"{name} ціна {place} грн за {unit or 'одиницю'}"
    fetch = _PROVIDERS[provider_name][0]
    try:
        results = fetch(query)
    except Exception as exc:  # мережа/ключ/ліміт — не валимо розрахунок
        return WebPrice(provider=provider_name, samples=[{"error": str(exc)[:200]}])

    samples: list[PriceSample] = []
    for r in results[:20]:
        text = f"{r.get('title', '')}. {r.get('snippet', '')}"
        samples.extend(extract_prices(text, unit, r.get("title", ""), r.get("url", "")))

    labor, material = _aggregate(samples)
    price = WebPrice(labor=labor, material=material, provider=provider_name,
                     samples=[{"value": s.value, "unit": s.unit, "kind": s.kind,
                               "title": s.title[:120], "url": s.url} for s in samples[:12]])
    if price.found:
        _cache_put(key, city, unit, price)
    return price
