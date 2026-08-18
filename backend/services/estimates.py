"""Робота з об'єктами та кошторисами: створення, наповнення, перерахунок, збереження."""
from __future__ import annotations

import json

from .. import db
from . import catalog, pricing
from .importer import ImportedDocument
from .normalize import clean_text, normalize_unit, parse_number


# ------------------------------------------------------------------------ об'єкти

def create_object(payload: dict) -> dict:
    name = clean_text(payload.get("name") or "")
    if not name:
        raise ValueError("Не вказано назву об'єкта")
    settings = pricing.settings_with_defaults(payload.get("settings"))
    object_id = db.execute(
        """INSERT INTO objects(name, address, city, region, customer, doc_code, settings)
           VALUES (?,?,?,?,?,?,?)""",
        (name, clean_text(payload.get("address", "")), clean_text(payload.get("city", "")),
         clean_text(payload.get("region", "")), clean_text(payload.get("customer", "")),
         clean_text(payload.get("doc_code", "")), json.dumps(settings, ensure_ascii=False)))
    return get_object(object_id)


def update_object(object_id: int, payload: dict) -> dict:
    current = get_object(object_id)
    if current is None:
        raise LookupError("Об'єкт не знайдено")
    settings = current["settings"]
    if "settings" in payload and payload["settings"] is not None:
        settings = pricing.settings_with_defaults({**settings, **payload["settings"]})
    db.execute(
        """UPDATE objects SET name=?, address=?, city=?, region=?, customer=?, doc_code=?,
                              settings=?, updated_at=datetime('now') WHERE id=?""",
        (clean_text(payload.get("name", current["name"])),
         clean_text(payload.get("address", current["address"])),
         clean_text(payload.get("city", current["city"])),
         clean_text(payload.get("region", current["region"])),
         clean_text(payload.get("customer", current["customer"])),
         clean_text(payload.get("doc_code", current["doc_code"])),
         json.dumps(settings, ensure_ascii=False), object_id))
    return get_object(object_id)


def get_object(object_id: int) -> dict | None:
    row = db.query_one("SELECT * FROM objects WHERE id = ?", (object_id,))
    if row is None:
        return None
    data = dict(row)
    data["settings"] = pricing.settings_with_defaults(db.load_json_field(data.get("settings"), {}))
    return data


def list_objects() -> list[dict]:
    rows = db.query("""
        SELECT o.*,
               (SELECT COUNT(*) FROM positions p
                  JOIN divisions d ON d.id = p.division_id
                  JOIN estimates e ON e.id = d.estimate_id
                 WHERE e.object_id = o.id) AS positions_count
          FROM objects o ORDER BY o.updated_at DESC, o.id DESC""")
    out = []
    for row in rows:
        data = dict(row)
        data["settings"] = pricing.settings_with_defaults(db.load_json_field(data.get("settings"), {}))
        data["totals"] = calculate_object(data["id"])["grand_total"]
        out.append(data)
    return out


def delete_object(object_id: int) -> None:
    db.execute("DELETE FROM objects WHERE id = ?", (object_id,))


# ------------------------------------------------------------- структура кошторису

def _next_ordinal(table: str, column: str, parent_id: int) -> int:
    row = db.query_one(f"SELECT COALESCE(MAX(ordinal), 0) AS m FROM {table} WHERE {column} = ?",
                       (parent_id,))
    return int(row["m"]) + 1


def add_estimate(object_id: int, code: str = "", title: str = "") -> int:
    return db.execute(
        "INSERT INTO estimates(object_id, code, title, ordinal) VALUES (?,?,?,?)",
        (object_id, clean_text(code), clean_text(title),
         _next_ordinal("estimates", "object_id", object_id)))


def add_division(estimate_id: int, code: str = "", title: str = "") -> int:
    return db.execute(
        "INSERT INTO divisions(estimate_id, code, title, ordinal) VALUES (?,?,?,?)",
        (estimate_id, clean_text(code), clean_text(title),
         _next_ordinal("divisions", "estimate_id", estimate_id)))


def _default_division(object_id: int) -> int:
    """Куди складати позиції, якщо користувач не створював розділів."""
    row = db.query_one(
        """SELECT d.id FROM divisions d JOIN estimates e ON e.id = d.estimate_id
            WHERE e.object_id = ? ORDER BY e.ordinal, d.ordinal LIMIT 1""", (object_id,))
    if row is not None:
        return row["id"]
    estimate_id = add_estimate(object_id, "01-01-01", "Локальний кошторис")
    return add_division(estimate_id, "1", "Роботи")


def add_position(object_id: int, payload: dict) -> dict:
    """Додає позицію; ціна визначається автоматично, якщо не задана вручну."""
    obj = get_object(object_id)
    if obj is None:
        raise LookupError("Об'єкт не знайдено")
    division_id = payload.get("division_id") or _default_division(object_id)
    name = clean_text(payload.get("name") or "")
    if not name:
        raise ValueError("Не вказано найменування роботи")
    unit = normalize_unit(payload.get("unit", ""))
    quantity = parse_number(payload.get("quantity")) or 0.0

    manual = bool(payload.get("manual"))
    labor = parse_number(payload.get("labor_price"))
    material = parse_number(payload.get("material_price"))
    machines = parse_number(payload.get("machines_price"))

    if manual and labor is not None:
        source, code, score = "manual", clean_text(payload.get("match_code", "")), 0.0
        material = material or 0.0
        machines = machines or 0.0
    else:
        res = pricing.resolve_price(name, unit, obj["city"], obj["region"],
                                    obj["settings"].get("price_strategy", pricing.DEFAULT_STRATEGY))
        labor, material, machines = res.labor, res.material, res.machines
        source, code, score = res.source, res.match_code, res.match_score
        if not unit and res.match_code:
            item = catalog.by_code(res.match_code)
            unit = item.get("unit", "") if item else unit

    position_id = db.execute(
        """INSERT INTO positions(division_id, ordinal, number, name, unit, quantity,
                                 labor_price, material_price, machines_price,
                                 price_source, match_code, match_score, manual, note)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (division_id, _next_ordinal("positions", "division_id", division_id),
         int(payload.get("number") or 0), name, unit, quantity,
         labor or 0.0, material or 0.0, machines or 0.0, source, code, score,
         1 if manual else 0, clean_text(payload.get("note", ""))))
    db.execute("UPDATE objects SET updated_at = datetime('now') WHERE id = ?", (object_id,))
    return dict(db.query_one("SELECT * FROM positions WHERE id = ?", (position_id,)))


def update_position(position_id: int, payload: dict) -> dict:
    row = db.query_one("SELECT * FROM positions WHERE id = ?", (position_id,))
    if row is None:
        raise LookupError("Позицію не знайдено")
    current = dict(row)
    fields, params = [], []

    for key in ("name", "unit", "note"):
        if key in payload:
            value = clean_text(payload[key])
            if key == "unit":
                value = normalize_unit(value)
            fields.append(f"{key} = ?")
            params.append(value)
    if "quantity" in payload:
        fields.append("quantity = ?")
        params.append(parse_number(payload["quantity"]) or 0.0)

    price_changed = False
    for key in ("labor_price", "material_price", "machines_price"):
        if key in payload and payload[key] is not None:
            fields.append(f"{key} = ?")
            params.append(parse_number(payload[key]) or 0.0)
            price_changed = True
    if price_changed:
        fields += ["manual = 1", "price_source = 'manual'"]
    if not fields:
        return current
    params.append(position_id)
    db.execute(f"UPDATE positions SET {', '.join(fields)} WHERE id = ?", params)
    return dict(db.query_one("SELECT * FROM positions WHERE id = ?", (position_id,)))


def delete_position(position_id: int) -> None:
    db.execute("DELETE FROM positions WHERE id = ?", (position_id,))


# --------------------------------------------------------------------- дерево

def build_tree(object_id: int) -> list[dict]:
    estimates = [dict(r) for r in db.query(
        "SELECT * FROM estimates WHERE object_id = ? ORDER BY ordinal, id", (object_id,))]
    for estimate in estimates:
        estimate["divisions"] = [dict(r) for r in db.query(
            "SELECT * FROM divisions WHERE estimate_id = ? ORDER BY ordinal, id", (estimate["id"],))]
        for division in estimate["divisions"]:
            division["positions"] = [dict(r) for r in db.query(
                "SELECT * FROM positions WHERE division_id = ? ORDER BY ordinal, id",
                (division["id"],))]
    return estimates


def calculate_object(object_id: int) -> dict:
    obj = get_object(object_id)
    if obj is None:
        raise LookupError("Об'єкт не знайдено")
    tree = build_tree(object_id)
    result = pricing.calculate(tree, obj["settings"])
    result["object"] = obj
    # Наскрізна нумерація позицій — як у відомості обсягів робіт.
    counter = 0
    for estimate in result["estimates"]:
        for division in estimate["divisions"]:
            for position in division["positions"]:
                counter += 1
                position["number"] = counter
    result["positions_count"] = counter
    return result


# ------------------------------------------------------------------ перерахунок

def reprice_object(object_id: int, strategy: str | None = None, keep_manual: bool = True,
                   use_web_cache: bool = True) -> dict:
    """Перераховує ціни всіх позицій об'єкта згідно з обраною стратегією джерел."""
    obj = get_object(object_id)
    if obj is None:
        raise LookupError("Об'єкт не знайдено")
    strategy = strategy or obj["settings"].get("price_strategy", pricing.DEFAULT_STRATEGY)
    stats = {"total": 0, "priced": 0, "skipped_manual": 0, "not_found": 0,
             "by_source": {}, "strategy": strategy}

    for estimate in build_tree(object_id):
        for division in estimate["divisions"]:
            for position in division["positions"]:
                stats["total"] += 1
                if keep_manual and position["manual"]:
                    stats["skipped_manual"] += 1
                    continue
                res = pricing.resolve_price(position["name"], position["unit"], obj["city"],
                                            obj["region"], strategy, use_web_cache=use_web_cache)
                if res.source == "none":
                    stats["not_found"] += 1
                else:
                    stats["priced"] += 1
                stats["by_source"][res.source_label] = stats["by_source"].get(res.source_label, 0) + 1
                db.execute(
                    """UPDATE positions SET labor_price=?, material_price=?, machines_price=?,
                                            price_source=?, match_code=?, match_score=?, manual=0
                        WHERE id = ?""",
                    (res.labor, res.material, res.machines, res.source, res.match_code,
                     res.match_score, position["id"]))
    db.execute("UPDATE objects SET updated_at = datetime('now') WHERE id = ?", (object_id,))
    return stats


def save_to_history(object_id: int) -> dict:
    """Фіксує ціни кошторису в історії, щоб наступні об'єкти могли їх використати."""
    obj = get_object(object_id)
    if obj is None:
        raise LookupError("Об'єкт не знайдено")
    saved = 0
    for estimate in build_tree(object_id):
        for division in estimate["divisions"]:
            for position in division["positions"]:
                if not (position["labor_price"] or position["material_price"]
                        or position["machines_price"]):
                    continue
                pricing.remember_price(
                    position["name"], position["unit"], obj["city"], obj["region"],
                    position["labor_price"], position["material_price"],
                    position["machines_price"], object_id,
                    source=position["price_source"] or "")
                saved += 1
    return {"saved": saved, "object_id": object_id}


# ------------------------------------------------------------------ імпорт ВОБ

def create_from_import(doc: ImportedDocument, overrides: dict | None = None,
                       auto_price: bool = True, strategy: str | None = None) -> dict:
    """Створює об'єкт із розібраної відомості обсягів робіт і одразу проставляє ціни."""
    overrides = overrides or {}
    payload = {
        "name": overrides.get("name") or doc.object_name or "Об'єкт без назви",
        "address": overrides.get("address") or doc.address,
        "city": overrides.get("city") or doc.city,
        "region": overrides.get("region") or doc.region,
        "customer": overrides.get("customer", ""),
        "doc_code": overrides.get("doc_code") or doc.doc_code,
        "settings": overrides.get("settings"),
    }
    obj = create_object(payload)
    object_id = obj["id"]

    estimate_id = None
    division_id = None
    for section in doc.sections:
        if section.kind == "estimate":
            estimate_id = add_estimate(object_id, section.code, section.title)
            division_id = None
        if section.kind == "division" or section.positions:
            if estimate_id is None:
                estimate_id = add_estimate(object_id, "", "Локальний кошторис")
            if section.kind == "division":
                division_id = add_division(estimate_id, section.code, section.title)
            elif division_id is None:
                division_id = add_division(estimate_id, "", "Роботи")
        for position in section.positions:
            db.execute(
                """INSERT INTO positions(division_id, ordinal, number, name, unit, quantity, note)
                   VALUES (?,?,?,?,?,?,?)""",
                (division_id, _next_ordinal("positions", "division_id", division_id),
                 position.number, position.name, position.unit, position.quantity, position.note))

    stats = reprice_object(object_id, strategy) if auto_price else {}
    return {"object": get_object(object_id), "stats": stats, "warnings": doc.warnings}
