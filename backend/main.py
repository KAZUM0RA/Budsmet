"""Budsmet — веб-застосунок для складання будівельних кошторисів.

Запуск:  uvicorn backend.main:app --reload
"""
from __future__ import annotations

import io
import re
from contextlib import asynccontextmanager
from datetime import date
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config, db
from .services import catalog, estimates, exporter, importer, pricing, web_prices
from .services.normalize import clean_text

@asynccontextmanager
async def lifespan(_app: FastAPI):
    db.init_db()
    catalog.items(refresh=True)
    yield


app = FastAPI(title="Budsmet", version="1.0",
              description="Складання кошторисів: обсяги робіт → ціни → заповнений кошторис",
              lifespan=lifespan)


# ------------------------------------------------------------------------ схеми

class ObjectIn(BaseModel):
    name: str
    address: str = ""
    city: str = ""
    region: str = ""
    customer: str = ""
    doc_code: str = ""
    settings: dict | None = None


class ObjectPatch(BaseModel):
    name: str | None = None
    address: str | None = None
    city: str | None = None
    region: str | None = None
    customer: str | None = None
    doc_code: str | None = None
    settings: dict | None = None


class PositionIn(BaseModel):
    name: str
    unit: str = ""
    quantity: float = 0
    division_id: int | None = None
    labor_price: float | None = None
    material_price: float | None = None
    machines_price: float | None = None
    manual: bool = False
    note: str = ""
    match_code: str = ""


class PositionPatch(BaseModel):
    name: str | None = None
    unit: str | None = None
    quantity: float | None = None
    labor_price: float | None = None
    material_price: float | None = None
    machines_price: float | None = None
    note: str | None = None


class SectionIn(BaseModel):
    code: str = ""
    title: str = ""


class CatalogIn(BaseModel):
    code: str = ""
    name: str
    unit: str = ""
    category: str = ""
    labor: float = 0
    material: float = 0
    machines: float = 0


class RepriceIn(BaseModel):
    strategy: str | None = None
    keep_manual: bool = True
    refresh_web: bool = False


# ------------------------------------------------------------------- довідники

@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "positions_in_catalog": len(catalog.items())}


@app.get("/api/meta")
def meta() -> dict:
    regions = catalog.regions()
    return {
        "defaults": config.DEFAULTS,
        "strategies": [
            {"key": "history_first", "label": "Спершу історія, потім інтернет, потім довідник"},
            {"key": "web_first", "label": "Спершу інтернет, потім історія, потім довідник"},
            {"key": "catalog_only", "label": "Тільки довідник розцінок"},
        ],
        "web_provider": web_prices.provider_status(),
        "regions": sorted(regions["regions"]),
        "cities": sorted(regions["cities"]),
        "categories": catalog.categories(),
        "catalog_size": len(catalog.items()),
        "units": ["м2", "м3", "м", "шт", "т", "кг", "компл", "блок", "агрегат", "кВт",
                  "грати", "кінців", "лінія", "вимір", "випроб", "точ", "%"],
    }


@app.get("/api/catalog")
def catalog_search(q: str = "", limit: int = Query(25, ge=1, le=200),
                   category: str = "") -> dict:
    items = catalog.items()
    if category:
        items = [i for i in items if i.get("category") == category]
    if not q.strip():
        return {"items": items[:limit], "total": len(items)}
    results = catalog.matcher().search(q, limit=limit * 3)
    seen, out = set(), []
    for res in results:
        if category and res.item.get("category") != category:
            continue
        if res.code in seen:
            continue
        seen.add(res.code)
        out.append({**res.item, "score": res.score})
        if len(out) >= limit:
            break
    return {"items": out, "total": len(out)}


@app.post("/api/catalog")
def catalog_upsert(payload: CatalogIn) -> dict:
    try:
        return catalog.upsert_override(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.delete("/api/catalog/{code}")
def catalog_delete(code: str) -> dict:
    catalog.delete_override(code)
    return {"deleted": code}


@app.get("/api/price")
def price_lookup(name: str, unit: str = "", city: str = "", region: str = "",
                 strategy: str = pricing.DEFAULT_STRATEGY) -> dict:
    """Показує, яку ціну і з якого джерела застосує програма для однієї роботи."""
    return pricing.resolve_price(name, unit, city, region, strategy).to_dict()


@app.get("/api/history")
def history(q: str = "", limit: int = Query(50, ge=1, le=500)) -> dict:
    if q.strip():
        rows = db.query(
            """SELECT * FROM price_history WHERE name LIKE ? ORDER BY created_at DESC LIMIT ?""",
            (f"%{q.strip()}%", limit))
    else:
        rows = db.query("SELECT * FROM price_history ORDER BY created_at DESC LIMIT ?", (limit,))
    return {"items": [dict(r) for r in rows]}


# --------------------------------------------------------------------- об'єкти

@app.get("/api/objects")
def objects_list() -> dict:
    return {"items": estimates.list_objects()}


@app.post("/api/objects")
def objects_create(payload: ObjectIn) -> dict:
    try:
        return estimates.create_object(payload.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/objects/{object_id}")
def objects_get(object_id: int) -> dict:
    try:
        return estimates.calculate_object(object_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.patch("/api/objects/{object_id}")
def objects_patch(object_id: int, payload: ObjectPatch) -> dict:
    data = {k: v for k, v in payload.model_dump().items() if v is not None}
    try:
        return estimates.update_object(object_id, data)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/api/objects/{object_id}")
def objects_delete(object_id: int) -> dict:
    estimates.delete_object(object_id)
    return {"deleted": object_id}


@app.post("/api/objects/{object_id}/estimates")
def estimate_add(object_id: int, payload: SectionIn) -> dict:
    estimate_id = estimates.add_estimate(object_id, payload.code, payload.title)
    return {"id": estimate_id}


@app.post("/api/estimates/{estimate_id}/divisions")
def division_add(estimate_id: int, payload: SectionIn) -> dict:
    division_id = estimates.add_division(estimate_id, payload.code, payload.title)
    return {"id": division_id}


@app.post("/api/objects/{object_id}/positions")
def position_add(object_id: int, payload: PositionIn) -> dict:
    try:
        return estimates.add_position(object_id, payload.model_dump())
    except (ValueError, LookupError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.patch("/api/positions/{position_id}")
def position_patch(position_id: int, payload: PositionPatch) -> dict:
    data = {k: v for k, v in payload.model_dump().items() if v is not None}
    try:
        return estimates.update_position(position_id, data)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.delete("/api/positions/{position_id}")
def position_delete(position_id: int) -> dict:
    estimates.delete_position(position_id)
    return {"deleted": position_id}


@app.post("/api/objects/{object_id}/reprice")
def object_reprice(object_id: int, payload: RepriceIn) -> dict:
    try:
        stats = estimates.reprice_object(object_id, payload.strategy, payload.keep_manual,
                                         use_web_cache=not payload.refresh_web)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"stats": stats, "calc": estimates.calculate_object(object_id)}


@app.post("/api/objects/{object_id}/history")
def object_to_history(object_id: int) -> dict:
    try:
        return estimates.save_to_history(object_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc


# ---------------------------------------------------------------------- експорт

_MEDIA = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pdf": "application/pdf",
    "html": "text/html; charset=utf-8",
}


def _safe_filename(name: str, ext: str) -> str:
    base = re.sub(r"[^\w\-. ]+", "", clean_text(name))[:60].strip() or "koshtorys"
    return f"{base} {date.today():%Y-%m-%d}.{ext}".replace(" ", "_")


@app.get("/api/objects/{object_id}/export/{fmt}")
def object_export(object_id: int, fmt: str):
    if fmt not in _MEDIA:
        raise HTTPException(400, "Підтримуються формати: xlsx, pdf, html")
    try:
        calc = estimates.calculate_object(object_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc

    if fmt == "xlsx":
        payload = exporter.to_xlsx(calc)
    elif fmt == "pdf":
        payload = exporter.to_pdf(calc)
    else:
        payload = exporter.to_html(calc).encode("utf-8")

    filename = _safe_filename(calc["object"]["name"], fmt)
    return StreamingResponse(
        io.BytesIO(payload), media_type=_MEDIA[fmt],
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"})


# ----------------------------------------------------------------------- імпорт

@app.post("/api/import/preview")
async def import_preview(file: UploadFile = File(...)) -> dict:
    """Розбирає порожню відомість обсягів робіт і показує, що знайдено, нічого не зберігаючи."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "Порожній файл")
    doc = importer.parse_document(file.filename or "", data)
    preview = doc.to_dict()
    # Одразу показуємо, яку розцінку програма підбере кожній позиції.
    matcher = catalog.matcher()
    for section in preview["sections"]:
        for position in section["positions"]:
            best = matcher.best(position["name"], position["unit"])
            position["match"] = ({"code": best.code, "name": best.name, "score": best.score}
                                 if best else None)
    return preview


@app.post("/api/import")
async def import_create(file: UploadFile = File(...), name: str = Form(""),
                        city: str = Form(""), region: str = Form(""),
                        customer: str = Form(""), address: str = Form(""),
                        strategy: str = Form(pricing.DEFAULT_STRATEGY),
                        auto_price: bool = Form(True)) -> dict:
    """Створює об'єкт із відомості обсягів робіт і одразу проставляє ціни."""
    data = await file.read()
    if not data:
        raise HTTPException(400, "Порожній файл")
    doc = importer.parse_document(file.filename or "", data)
    if not doc.positions:
        raise HTTPException(422, "У файлі не знайдено позицій з обсягами. "
                                 "Перевірте, що це відомість обсягів робіт (PDF/XLSX/CSV).")
    result = estimates.create_from_import(
        doc, {"name": name, "city": city, "region": region,
              "customer": customer, "address": address},
        auto_price=auto_price, strategy=strategy)
    result["calc"] = estimates.calculate_object(result["object"]["id"])
    return result


@app.post("/api/import/fill")
async def import_fill(file: UploadFile = File(...), fmt: str = Form("xlsx"),
                      city: str = Form(""), region: str = Form(""),
                      strategy: str = Form(pricing.DEFAULT_STRATEGY),
                      keep: bool = Form(True)):
    """Один крок: порожня відомість на вході — заповнений кошторис на виході."""
    if fmt not in _MEDIA:
        raise HTTPException(400, "Підтримуються формати: xlsx, pdf, html")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Порожній файл")
    doc = importer.parse_document(file.filename or "", data)
    if not doc.positions:
        raise HTTPException(422, "У файлі не знайдено позицій з обсягами.")

    result = estimates.create_from_import(doc, {"city": city, "region": region},
                                          auto_price=True, strategy=strategy)
    object_id = result["object"]["id"]
    calc = estimates.calculate_object(object_id)
    payload = (exporter.to_xlsx(calc) if fmt == "xlsx"
               else exporter.to_pdf(calc) if fmt == "pdf"
               else exporter.to_html(calc).encode("utf-8"))
    if not keep:
        estimates.delete_object(object_id)

    filename = _safe_filename(calc["object"]["name"], fmt)
    return StreamingResponse(
        io.BytesIO(payload), media_type=_MEDIA[fmt],
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}",
            "X-Budsmet-Object-Id": str(object_id) if keep else "",
            "X-Budsmet-Total": str(calc["grand_total"]),
            "X-Budsmet-Positions": str(calc["positions_count"]),
        })


# ------------------------------------------------------------------- статика

@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(config.FRONTEND_DIR / "index.html")


if config.FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=config.FRONTEND_DIR), name="static")
