"""Рушій ціноутворення: звідки взяти ціну та як порахувати підсумки кошторису."""
from __future__ import annotations

import statistics
from dataclasses import asdict, dataclass, field

from .. import config, db
from . import catalog, web_prices
from .matcher import work_key
from .normalize import clean_text, normalize_unit

# Порядок джерел ціни.
STRATEGIES = {
    "history_first": ["history", "web", "catalog"],   # спершу свої старі кошториси
    "web_first": ["web", "history", "catalog"],       # спершу ринок в інтернеті
    "catalog_only": ["catalog"],                      # тільки довідник
}
DEFAULT_STRATEGY = "history_first"

SOURCE_LABELS = {
    "history": "історія (старі кошториси)",
    "web": "інтернет",
    "catalog": "довідник",
    "manual": "ручне введення",
    "none": "не знайдено",
}


@dataclass
class PriceResolution:
    labor: float = 0.0
    material: float = 0.0
    machines: float = 0.0
    source: str = "none"
    source_label: str = SOURCE_LABELS["none"]
    match_code: str = ""
    match_name: str = ""
    match_score: float = 0.0
    region_factor: float = 1.0
    region_label: str = ""
    details: dict = field(default_factory=dict)

    @property
    def unit_total(self) -> float:
        return round(self.labor + self.material + self.machines, 4)

    def to_dict(self) -> dict:
        data = asdict(self)
        data["unit_total"] = self.unit_total
        return data


# --------------------------------------------------------------------- історія

def history_price(name: str, unit: str, city: str = "", region: str = "") -> dict | None:
    """Медіана останніх фактичних цін по цій роботі: спершу по місту, потім по області, потім будь-де."""
    key = work_key(name, unit)
    attempts = [
        ("місто " + city, "AND city = ?", (key, city)) if city else None,
        ("область " + region, "AND region = ?", (key, region)) if region else None,
        ("уся історія", "", (key,)),
    ]
    for attempt in attempts:
        if attempt is None:
            continue
        scope, clause, params = attempt
        rows = db.query(
            f"""SELECT labor_price, material_price, machines_price, created_at, city
                FROM price_history WHERE work_key = ? {clause}
                ORDER BY created_at DESC, id DESC LIMIT ?""",
            (*params, config.HISTORY_WINDOW))
        if not rows:
            continue
        return {
            "labor": round(statistics.median([r["labor_price"] for r in rows]), 2),
            "material": round(statistics.median([r["material_price"] for r in rows]), 2),
            "machines": round(statistics.median([r["machines_price"] for r in rows]), 2),
            "samples": len(rows),
            "scope": scope,
            "last_used": rows[0]["created_at"],
        }
    return None


def remember_price(name: str, unit: str, city: str, region: str, labor: float,
                   material: float, machines: float, object_id: int | None = None,
                   source: str = "") -> None:
    """Записує ціну позиції в історію — наступні кошториси зможуть на неї спертись."""
    if not (labor or material or machines):
        return
    db.execute(
        """INSERT INTO price_history(work_key, name, unit, city, region, labor_price,
                                     material_price, machines_price, object_id, source)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (work_key(name, unit), clean_text(name), normalize_unit(unit), clean_text(city),
         clean_text(region), float(labor), float(material), float(machines), object_id, source))


# ------------------------------------------------------------------ визначення ціни

def resolve_price(name: str, unit: str, city: str = "", region: str = "",
                  strategy: str = DEFAULT_STRATEGY, use_web_cache: bool = True) -> PriceResolution:
    """Головна функція: повертає ціну за одиницю та джерело, з якого вона взята."""
    unit = normalize_unit(unit)
    factor, region_label = catalog.region_factor(city, region)
    res = PriceResolution(region_factor=factor, region_label=region_label)

    match = catalog.matcher().best(name, unit)
    if match is not None:
        res.match_code, res.match_name, res.match_score = match.code, match.name, match.score

    for source in STRATEGIES.get(strategy, STRATEGIES[DEFAULT_STRATEGY]):
        if source == "history":
            hit = history_price(name, unit, city, region)
            if hit:
                res.labor, res.material, res.machines = hit["labor"], hit["material"], hit["machines"]
                res.source = "history"
                res.details = {"samples": hit["samples"], "scope": hit["scope"],
                               "last_used": hit["last_used"]}
                break
        elif source == "web":
            hit = web_prices.lookup(name, unit, city, region, use_cache=use_web_cache)
            if hit.found:
                res.labor, res.material = hit.labor, hit.material
                res.machines = 0.0
                res.source = "web"
                res.details = {"provider": hit.provider, "cached": hit.cached,
                               "samples": hit.samples[:6]}
                break
        elif source == "catalog" and match is not None:
            item = match.item
            res.labor = round(float(item.get("labor", 0)) * factor, 2)
            res.material = round(float(item.get("material", 0)) * factor, 2)
            res.machines = round(float(item.get("machines", 0)) * factor, 2)
            res.source = "catalog"
            res.details = {"code": item["code"], "category": item.get("category", ""),
                           "base_labor": item.get("labor", 0), "factor": factor,
                           "score": match.score}
            break

    res.source_label = SOURCE_LABELS.get(res.source, res.source)
    return res


# --------------------------------------------------------------------- підсумки

def _num(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def position_totals(position: dict, materials_included: bool = True) -> dict:
    """Вартість однієї позиції з розкладкою на роботи/матеріали/машини."""
    qty = _num(position.get("quantity"))
    labor = _num(position.get("labor_price"))
    material = _num(position.get("material_price")) if materials_included else 0.0
    machines = _num(position.get("machines_price"))
    unit_price = round(labor + material + machines, 2)
    return {
        "unit_price": unit_price,
        "labor_total": round(labor * qty, 2),
        "material_total": round(material * qty, 2),
        "machines_total": round(machines * qty, 2),
        "total": round(unit_price * qty, 2),
    }


def settings_with_defaults(settings: dict | None) -> dict:
    merged = dict(config.DEFAULTS)
    merged.update(settings or {})
    return merged


def calculate(estimate_tree: list[dict], settings: dict | None = None) -> dict:
    """Рахує підсумки по розділах, локальних кошторисах та об'єкту в цілому.

    `estimate_tree` — список локальних кошторисів:
        [{"code","title","divisions":[{"code","title","positions":[...]}]}]
    Дерево доповнюється полями totals прямо на місці (і повертається у результаті).
    """
    cfg = settings_with_defaults(settings)
    materials = bool(cfg.get("materials_included", True))
    direct = {"labor": 0.0, "material": 0.0, "machines": 0.0, "total": 0.0}

    for estimate in estimate_tree:
        est_sum = {"labor": 0.0, "material": 0.0, "machines": 0.0, "total": 0.0}
        for division in estimate.get("divisions", []):
            div_sum = {"labor": 0.0, "material": 0.0, "machines": 0.0, "total": 0.0}
            for position in division.get("positions", []):
                totals = position_totals(position, materials)
                position["totals"] = totals
                div_sum["labor"] += totals["labor_total"]
                div_sum["material"] += totals["material_total"]
                div_sum["machines"] += totals["machines_total"]
                div_sum["total"] += totals["total"]
            division["totals"] = {k: round(v, 2) for k, v in div_sum.items()}
            for k in est_sum:
                est_sum[k] += div_sum[k]
        estimate["totals"] = {k: round(v, 2) for k, v in est_sum.items()}
        for k in direct:
            direct[k] += est_sum[k]

    direct = {k: round(v, 2) for k, v in direct.items()}
    # ЗВВ і прибуток нараховуються на вартість робіт та експлуатації машин,
    # матеріали в базу не входять (як і в АВК-5).
    work_base = round(direct["labor"] + direct["machines"], 2)
    overhead = round(work_base * _num(cfg["overhead_pct"]) / 100, 2)
    subtotal = round(direct["total"] + overhead, 2)
    profit = round((work_base + overhead) * _num(cfg["profit_pct"]) / 100, 2)
    admin = round(subtotal * _num(cfg["admin_pct"]) / 100, 2)
    risk = round(subtotal * _num(cfg["risk_pct"]) / 100, 2)
    before_vat = round(subtotal + profit + admin + risk, 2)
    vat = round(before_vat * _num(cfg["vat_pct"]) / 100, 2)

    return {
        "settings": cfg,
        "estimates": estimate_tree,
        "direct": direct,
        "lines": [
            {"key": "direct", "label": "Прямі витрати", "value": direct["total"]},
            {"key": "labor", "label": "у т.ч. вартість робіт", "value": direct["labor"], "sub": True},
            {"key": "material", "label": "у т.ч. вартість матеріалів", "value": direct["material"], "sub": True},
            {"key": "machines", "label": "у т.ч. експлуатація машин", "value": direct["machines"], "sub": True},
            {"key": "overhead", "label": f"Загальновиробничі витрати ({cfg['overhead_pct']}%)", "value": overhead},
            {"key": "subtotal", "label": "Разом", "value": subtotal},
            {"key": "profit", "label": f"Кошторисний прибуток ({cfg['profit_pct']}%)", "value": profit},
            {"key": "admin", "label": f"Адміністративні витрати ({cfg['admin_pct']}%)", "value": admin},
            {"key": "risk", "label": f"Кошти на покриття ризиків ({cfg['risk_pct']}%)", "value": risk},
            {"key": "before_vat", "label": "Разом без ПДВ", "value": before_vat},
            {"key": "vat", "label": f"ПДВ ({cfg['vat_pct']}%)", "value": vat},
            {"key": "total", "label": "Усього кошторисна вартість", "value": round(before_vat + vat, 2)},
        ],
        "overhead": overhead,
        "profit": profit,
        "admin": admin,
        "risk": risk,
        "before_vat": before_vat,
        "vat": vat,
        "grand_total": round(before_vat + vat, 2),
    }
