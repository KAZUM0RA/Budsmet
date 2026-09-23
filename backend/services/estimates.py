"""Робота з об'єктами та кошторисами: створення, наповнення, перерахунок, збереження."""
from __future__ import annotations

import json

from .. import config, db
from . import catalog, pricing
from . import web_prices
from .web_prices import WebBudget
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
    # Один бюджет живих запитів на весь перерахунок — щоб великий кошторис
    # не вичерпав місячний ліміт пошукового сервісу за один клік.
    budget = WebBudget(limit=config.WEB_MAX_QUERIES)
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
                                            obj["region"], strategy,
                                            use_web_cache=use_web_cache, budget=budget)
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
    stats["web"] = budget.to_dict()
    return stats


# Межа, вище якої підібраній розцінці можна довіряти без перегляду.
CONFIDENT_SCORE = 80.0


def match_from_catalog(object_id: int, keep_manual: bool = True,
                       min_score: float | None = None) -> dict:
    """Підбирає ціни всіх позицій із довідника за схожістю найменувань.

    Повертає статистику і перелік позицій, які варто переглянути: для них
    додаються найкращі варіанти з довідника, щоб можна було обрати вручну.
    """
    obj = get_object(object_id)
    if obj is None:
        raise LookupError("Об'єкт не знайдено")
    matcher = catalog.matcher()
    factor, region_label = catalog.region_factor(obj["city"], obj["region"])
    threshold = catalog.matcher().MIN_SCORE if min_score is None else float(min_score)

    stats = {"total": 0, "applied": 0, "confident": 0, "review": 0,
             "not_found": 0, "skipped_manual": 0,
             "region_factor": factor, "region_label": region_label}
    review: list[dict] = []

    for estimate in build_tree(object_id):
        for division in estimate["divisions"]:
            for position in division["positions"]:
                stats["total"] += 1
                if keep_manual and position["manual"]:
                    stats["skipped_manual"] += 1
                    continue

                best = matcher.best(position["name"], position["unit"])
                if best is not None and best.score >= threshold:
                    item = best.item
                    db.execute(
                        """UPDATE positions SET labor_price=?, material_price=?,
                               machines_price=?, price_source='catalog',
                               match_code=?, match_score=?, manual=0
                           WHERE id = ?""",
                        (round(float(item.get("labor", 0)) * factor, 2),
                         round(float(item.get("material", 0)) * factor, 2),
                         round(float(item.get("machines", 0)) * factor, 2),
                         item["code"], best.score, position["id"]))
                    stats["applied"] += 1
                    if best.score >= CONFIDENT_SCORE:
                        stats["confident"] += 1
                        continue
                    stats["review"] += 1
                else:
                    stats["not_found"] += 1

                # Сумнівні та непідібрані — на перегляд, з варіантами на вибір.
                review.append({
                    "position_id": position["id"],
                    "number": position["number"],
                    "name": position["name"],
                    "unit": position["unit"],
                    "quantity": position["quantity"],
                    "applied_code": best.code if (best and best.score >= threshold) else "",
                    "score": best.score if best else 0.0,
                    "candidates": [
                        {"code": c.code, "name": c.name, "unit": c.unit,
                         "score": c.score,
                         "labor": round(float(c.item.get("labor", 0)) * factor, 2),
                         "material": round(float(c.item.get("material", 0)) * factor, 2),
                         "machines": round(float(c.item.get("machines", 0)) * factor, 2)}
                        for c in matcher.candidates(position["name"], position["unit"], limit=6)
                    ],
                })

    db.execute("UPDATE objects SET updated_at = datetime('now') WHERE id = ?", (object_id,))
    return {"stats": stats, "review": review}


def apply_catalog_item(position_id: int, code: str) -> dict:
    """Ставить позиції ціну з обраної розцінки довідника."""
    row = db.query_one(
        """SELECT p.*, o.city AS city, o.region AS region
             FROM positions p
             JOIN divisions d ON d.id = p.division_id
             JOIN estimates e ON e.id = d.estimate_id
             JOIN objects o ON o.id = e.object_id
            WHERE p.id = ?""", (position_id,))
    if row is None:
        raise LookupError("Позицію не знайдено")
    item = catalog.by_code(code)
    if item is None:
        raise LookupError("Розцінку не знайдено в довіднику")

    factor, _label = catalog.region_factor(row["city"], row["region"])
    db.execute(
        """UPDATE positions SET labor_price=?, material_price=?, machines_price=?,
               unit = CASE WHEN unit = '' THEN ? ELSE unit END,
               price_source='catalog', match_code=?, match_score=100.0, manual=0
           WHERE id = ?""",
        (round(float(item.get("labor", 0)) * factor, 2),
         round(float(item.get("material", 0)) * factor, 2),
         round(float(item.get("machines", 0)) * factor, 2),
         item.get("unit", ""), code, position_id))
    return dict(db.query_one("SELECT * FROM positions WHERE id = ?", (position_id,)))


def position_candidates(position_id: int, limit: int = 6) -> list[dict]:
    """Варіанти розцінок для однієї позиції — для ручного вибору."""
    row = db.query_one(
        """SELECT p.name, p.unit, o.city AS city, o.region AS region
             FROM positions p
             JOIN divisions d ON d.id = p.division_id
             JOIN estimates e ON e.id = d.estimate_id
             JOIN objects o ON o.id = e.object_id
            WHERE p.id = ?""", (position_id,))
    if row is None:
        raise LookupError("Позицію не знайдено")
    factor, _label = catalog.region_factor(row["city"], row["region"])
    return [
        {"code": c.code, "name": c.name, "unit": c.unit, "score": c.score,
         "labor": round(float(c.item.get("labor", 0)) * factor, 2),
         "material": round(float(c.item.get("material", 0)) * factor, 2),
         "machines": round(float(c.item.get("machines", 0)) * factor, 2)}
        for c in catalog.matcher().candidates(row["name"], row["unit"], limit=limit)
    ]


def _position_with_object(position_id: int):
    return db.query_one(
        """SELECT p.*, o.id AS object_id, o.city AS city, o.region AS region
             FROM positions p
             JOIN divisions d ON d.id = p.division_id
             JOIN estimates e ON e.id = d.estimate_id
             JOIN objects o ON o.id = e.object_id
            WHERE p.id = ?""", (position_id,))


def save_position_to_catalog(position_id: int, mode: str = "auto",
                             category: str = "") -> dict:
    """Записує ціну позиції в довідник розцінок.

    Ціни в довіднику базові, а в кошторисі вже помножені на регіональний
    коефіцієнт міста. Тому назад записується ціна, поділена на цей коефіцієнт —
    інакше з кожним записом розцінка зростала б на коефіцієнт.

    mode: "auto" — оновити підібрану розцінку, а якщо її немає, створити нову;
          "update" — лише оновити підібрану; "new" — завжди створити нову.
    """
    row = _position_with_object(position_id)
    if row is None:
        raise LookupError("Позицію не знайдено")
    name = clean_text(row["name"])
    if not name:
        raise ValueError("У позиції немає найменування")

    factor, region_label = catalog.region_factor(row["city"], row["region"])
    factor = factor or 1.0

    def base(value) -> float:
        return round(float(value or 0) / factor, 2)

    code = clean_text(row["match_code"] or "")
    if mode == "new" or (mode == "auto" and not code):
        code = ""
    elif mode == "update" and not code:
        raise ValueError("Для цієї позиції немає підібраної розцінки — створіть нову")

    existing = catalog.by_code(code) if code else None
    payload = {
        "code": code,
        "name": existing["name"] if existing else name,
        "unit": (existing.get("unit") if existing else "") or row["unit"],
        "category": clean_text(category) or (existing.get("category") if existing else "")
                    or "Ручні розцінки",
        "labor": base(row["labor_price"]),
        "material": base(row["material_price"]),
        "machines": base(row["machines_price"]),
    }
    item = catalog.upsert_override(payload)

    # Позиція тепер спирається на розцінку довідника, а не на ручне значення.
    db.execute(
        "UPDATE positions SET match_code=?, match_score=100.0 WHERE id=?",
        (item["code"], position_id))
    return {
        "item": item,
        "created": existing is None,
        "region_factor": factor,
        "region_label": region_label,
        "price_in_estimate": round(float(row["labor_price"] or 0)
                                   + float(row["material_price"] or 0)
                                   + float(row["machines_price"] or 0), 2),
    }


def _already_in_catalog(position: dict, obj: dict) -> bool:
    """Чи збігається ціна позиції з тією, що вже записана в довіднику."""
    code = clean_text(position.get("match_code") or "")
    if not code:
        return False
    item = catalog.by_code(code)
    if item is None:
        return False
    factor, _label = catalog.region_factor(obj["city"], obj["region"])
    factor = factor or 1.0
    pairs = (("labor", "labor_price"), ("material", "material_price"),
             ("machines", "machines_price"))
    return all(
        abs(float(item.get(field, 0)) - round(float(position[column] or 0) / factor, 2)) < 0.01
        for field, column in pairs)


def save_manual_prices_to_catalog(object_id: int, category: str = "") -> dict:
    """Записує в довідник усі ціни об'єкта, введені вручну."""
    obj = get_object(object_id)
    if obj is None:
        raise LookupError("Об'єкт не знайдено")
    stats = {"updated": 0, "created": 0, "unchanged": 0, "skipped": 0, "items": []}

    for estimate in build_tree(object_id):
        for division in estimate["divisions"]:
            for position in division["positions"]:
                if not position["manual"]:
                    continue
                if not (position["labor_price"] or position["material_price"]
                        or position["machines_price"]):
                    stats["skipped"] += 1
                    continue
                # Якщо в довіднику вже стоїть саме ця ціна, писати нічого.
                if _already_in_catalog(position, obj):
                    stats["unchanged"] += 1
                    continue
                result = save_position_to_catalog(position["id"], mode="auto",
                                                  category=category)
                stats["created" if result["created"] else "updated"] += 1
                stats["items"].append({
                    "position_id": position["id"],
                    "name": position["name"],
                    "code": result["item"]["code"],
                    "labor": result["item"]["labor"],
                    "material": result["item"]["material"],
                    "created": result["created"],
                })
    stats["region_factor"], stats["region_label"] = catalog.region_factor(
        obj["city"], obj["region"])
    return stats


def price_unknown_from_web(object_id: int) -> dict:
    """Шукає ціни в інтернеті лише для позицій, що лишились без ціни.

    Саме цей режим має сенс на платних пошукових сервісах: запит витрачається
    тільки там, де ні історія, ні довідник, ні прайс сайту роботи не знають.
    """
    obj = get_object(object_id)
    if obj is None:
        raise LookupError("Об'єкт не знайдено")

    status = web_prices.provider_status()
    if not status["enabled"]:
        return {"enabled": False, "reason": status["reason"],
                "provider": status["provider"], "stats": None}

    budget = WebBudget(limit=config.WEB_MAX_QUERIES)
    stats = {"candidates": 0, "priced": 0, "not_found": 0, "provider": status["provider"]}
    found: list[dict] = []

    for estimate in build_tree(object_id):
        for division in estimate["divisions"]:
            for position in division["positions"]:
                if position["manual"]:
                    continue
                if (position["labor_price"] or position["material_price"]
                        or position["machines_price"]):
                    continue
                stats["candidates"] += 1
                hit = web_prices.lookup(position["name"], position["unit"],
                                        obj["city"], obj["region"], budget=budget)
                if not hit.found:
                    stats["not_found"] += 1
                    continue
                db.execute(
                    """UPDATE positions SET labor_price=?, material_price=?,
                           machines_price=0, price_source='web', manual=0
                       WHERE id = ?""",
                    (hit.labor, hit.material, position["id"]))
                stats["priced"] += 1
                found.append({
                    "position_id": position["id"], "number": position["number"],
                    "name": position["name"], "unit": position["unit"],
                    "labor": hit.labor, "material": hit.material,
                    "cached": hit.cached,
                    "samples": [s for s in hit.samples if isinstance(s, dict)][:3],
                })

    db.execute("UPDATE objects SET updated_at = datetime('now') WHERE id = ?", (object_id,))
    stats["web"] = budget.to_dict()
    return {"enabled": True, "stats": stats, "found": found}


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
