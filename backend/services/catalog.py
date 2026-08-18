"""Довідник розцінок: JSON-база + користувацькі перевизначення в БД + регіони."""
from __future__ import annotations

import json
import threading

from .. import db
from ..config import DATA_DIR
from .matcher import WorkMatcher
from .normalize import clean_text, normalize_unit

_lock = threading.RLock()  # reentrant: matcher() викликає items() під тим самим замком
_cache: dict = {"items": None, "matcher": None, "regions": None}


def _load_json(name: str) -> dict:
    with open(DATA_DIR / name, encoding="utf-8") as fh:
        return json.load(fh)


def regions() -> dict:
    if _cache["regions"] is None:
        _cache["regions"] = _load_json("regions.json")
    return _cache["regions"]


def region_factor(city: str = "", region: str = "") -> tuple[float, str]:
    """Регіональний коефіцієнт до базових розцінок + пояснення, звідки він."""
    data = regions()
    city = clean_text(city)
    region = clean_text(region)
    if city and city in data["cities"]:
        return float(data["cities"][city]), f"місто {city}"
    if region and region in data["regions"]:
        return float(data["regions"][region]), region
    if region:
        for name, factor in data["regions"].items():
            if name.split()[0].lower() in region.lower():
                return float(factor), name
    return 1.0, "базовий регіон"


def items(refresh: bool = False) -> list[dict]:
    """Позиції довідника: JSON-база, поверх якої накладені правки користувача."""
    with _lock:
        if _cache["items"] is None or refresh:
            base = _load_json("price_catalog.json")["items"]
            merged = {i["code"]: dict(i) for i in base}
            for row in db.query("SELECT * FROM catalog_overrides"):
                code = row["code"]
                entry = merged.get(code, {})
                entry.update({
                    "code": code, "name": row["name"],
                    "unit": normalize_unit(row["unit"]),
                    "category": row["category"] or entry.get("category", ""),
                    "labor": float(row["labor"]), "material": float(row["material"]),
                    "machines": float(row["machines"]),
                    "source": "довідник (правка користувача)",
                })
                merged[code] = entry
            _cache["items"] = list(merged.values())
            _cache["matcher"] = None
        return _cache["items"]


def matcher() -> WorkMatcher:
    with _lock:
        if _cache["matcher"] is None:
            _cache["matcher"] = WorkMatcher(_cache["items"] or items())
        return _cache["matcher"]


def invalidate() -> None:
    with _lock:
        _cache["items"] = None
        _cache["matcher"] = None


def by_code(code: str) -> dict | None:
    for item in items():
        if item["code"] == code:
            return item
    return None


def categories() -> list[str]:
    return sorted({i.get("category", "") for i in items() if i.get("category")})


def upsert_override(payload: dict) -> dict:
    """Додає або змінює розцінку в довіднику."""
    code = clean_text(payload.get("code") or "")
    name = clean_text(payload.get("name") or "")
    if not name:
        raise ValueError("Не вказано найменування роботи")
    if not code:
        existing = {i["code"] for i in items()}
        n = 1
        while f"U{n:04d}" in existing:
            n += 1
        code = f"U{n:04d}"
    db.execute(
        """INSERT INTO catalog_overrides(code, name, unit, category, labor, material, machines, updated_at)
           VALUES (?,?,?,?,?,?,?, datetime('now'))
           ON CONFLICT(code) DO UPDATE SET name=excluded.name, unit=excluded.unit,
             category=excluded.category, labor=excluded.labor, material=excluded.material,
             machines=excluded.machines, updated_at=datetime('now')""",
        (code, name, normalize_unit(payload.get("unit", "")), clean_text(payload.get("category", "")),
         float(payload.get("labor") or 0), float(payload.get("material") or 0),
         float(payload.get("machines") or 0)),
    )
    invalidate()
    return by_code(code)


def delete_override(code: str) -> None:
    db.execute("DELETE FROM catalog_overrides WHERE code = ?", (code,))
    invalidate()
