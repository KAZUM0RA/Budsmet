"""Наскрізні сценарії через HTTP API: порожня відомість → заповнений кошторис."""
import io

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from tests.conftest import SAMPLES


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def test_health_and_meta(client):
    assert client.get("/api/health").json()["status"] == "ok"
    meta = client.get("/api/meta").json()
    assert meta["catalog_size"] > 150
    assert {s["key"] for s in meta["strategies"]} == {"history_first", "web_first", "catalog_only"}
    assert meta["web_provider"]["enabled"] is False   # типово інтернет-аналіз вимкнено


def test_import_preview_does_not_persist(client, clean_db):
    data = (SAMPLES / "vob_118_semenivka.pdf").read_bytes()
    response = client.post("/api/import/preview",
                           files={"file": ("vob.pdf", io.BytesIO(data), "application/pdf")})
    preview = response.json()
    assert preview["total_positions"] == 116
    assert preview["city"] == "Семенівка"
    # Кожна позиція одразу показує підібрану розцінку.
    matched = [p for s in preview["sections"] for p in s["positions"] if p["match"]]
    assert len(matched) == 116
    assert client.get("/api/objects").json()["items"] == []


def test_import_creates_priced_object(client, clean_db):
    data = (SAMPLES / "vob_174_lokhvytsia.pdf").read_bytes()
    result = client.post("/api/import",
                         files={"file": ("vob.pdf", io.BytesIO(data), "application/pdf")},
                         data={"strategy": "catalog_only"}).json()
    stats = result["stats"]
    assert stats["total"] == 129 and stats["priced"] == 129 and stats["not_found"] == 0
    calc = result["calc"]
    assert calc["positions_count"] == 129
    assert calc["grand_total"] > 0
    assert calc["object"]["city"] == "Лохвиця"
    # Підсумок = сума всіх позицій + нарахування.
    assert calc["direct"]["total"] > 0
    assert calc["grand_total"] > calc["direct"]["total"]


def test_one_step_fill_returns_xlsx(client, clean_db):
    data = (SAMPLES / "vob_118_semenivka.pdf").read_bytes()
    response = client.post("/api/import/fill",
                           files={"file": ("vob.pdf", io.BytesIO(data), "application/pdf")},
                           data={"fmt": "xlsx", "keep": "false"})
    assert response.status_code == 200
    assert response.headers["x-budsmet-positions"] == "116"
    assert float(response.headers["x-budsmet-total"]) > 0
    assert response.content[:2] == b"PK"          # це справжній xlsx
    assert client.get("/api/objects").json()["items"] == []   # keep=false прибирає об'єкт


def test_import_rejects_file_without_positions(client, clean_db):
    response = client.post("/api/import",
                           files={"file": ("empty.csv", io.BytesIO(b"just text\n"), "text/csv")})
    assert response.status_code == 422


def test_manual_object_lifecycle(client, clean_db):
    obj = client.post("/api/objects", json={
        "name": "Ремонт офісу", "city": "Київ", "region": "Київська область"}).json()
    oid = obj["id"]

    added = client.post(f"/api/objects/{oid}/positions", json={
        "name": "Демонтаж розеток", "unit": "шт", "quantity": 20}).json()
    assert added["price_source"] == "catalog"
    assert added["labor_price"] == pytest.approx(55 * 1.35)   # київський коефіцієнт

    calc = client.get(f"/api/objects/{oid}").json()
    assert calc["positions_count"] == 1
    position = calc["estimates"][0]["divisions"][0]["positions"][0]
    assert position["totals"]["total"] == pytest.approx(added["labor_price"] * 20)

    # Ручна ціна перекриває автоматичну і не збивається перерахунком.
    client.patch(f"/api/positions/{position['id']}", json={"labor_price": 500})
    client.post(f"/api/objects/{oid}/reprice", json={"strategy": "catalog_only", "keep_manual": True})
    calc = client.get(f"/api/objects/{oid}").json()
    position = calc["estimates"][0]["divisions"][0]["positions"][0]
    assert position["labor_price"] == 500
    assert position["price_source"] == "manual"

    client.delete(f"/api/positions/{position['id']}")
    assert client.get(f"/api/objects/{oid}").json()["positions_count"] == 0


def test_history_round_trip_changes_price_source(client, clean_db):
    obj = client.post("/api/objects", json={"name": "Об'єкт 1", "city": "Суми"}).json()
    client.post(f"/api/objects/{obj['id']}/positions",
                json={"name": "Демонтаж розеток", "unit": "шт", "quantity": 5})
    client.patch(f"/api/objects/{obj['id']}", json={})   # без змін
    saved = client.post(f"/api/objects/{obj['id']}/history").json()
    assert saved["saved"] == 1

    other = client.post("/api/objects", json={"name": "Об'єкт 2", "city": "Суми"}).json()
    added = client.post(f"/api/objects/{other['id']}/positions",
                        json={"name": "Демонтаж розеток", "unit": "шт", "quantity": 3}).json()
    assert added["price_source"] == "history"


def test_settings_affect_totals(client, clean_db):
    obj = client.post("/api/objects", json={"name": "Об'єкт", "city": "Полтава"}).json()
    client.post(f"/api/objects/{obj['id']}/positions",
                json={"name": "Демонтаж розеток", "unit": "шт", "quantity": 10})
    before = client.get(f"/api/objects/{obj['id']}").json()["grand_total"]
    client.patch(f"/api/objects/{obj['id']}", json={"settings": {"vat_pct": 0}})
    after = client.get(f"/api/objects/{obj['id']}").json()
    assert after["vat"] == 0
    assert after["grand_total"] < before


def test_exports_in_all_formats(client, clean_db):
    obj = client.post("/api/objects", json={"name": "Об'єкт для вивантаження",
                                            "city": "Полтава"}).json()
    client.post(f"/api/objects/{obj['id']}/positions",
                json={"name": "Улаштування бетонної стяжки товщиною 20 мм площею до 20 м2",
                      "unit": "м2", "quantity": 25})

    xlsx = client.get(f"/api/objects/{obj['id']}/export/xlsx")
    assert xlsx.status_code == 200 and xlsx.content[:2] == b"PK"

    pdf = client.get(f"/api/objects/{obj['id']}/export/pdf")
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"

    html = client.get(f"/api/objects/{obj['id']}/export/html")
    assert "Улаштування бетонної стяжки" in html.text
    assert "Усього кошторисна вартість" in html.text

    assert client.get(f"/api/objects/{obj['id']}/export/docx").status_code == 400


def test_catalog_override_is_used_for_pricing(client, clean_db):
    client.post("/api/catalog", json={"name": "Унікальна авторська робота", "unit": "м2",
                                      "category": "Тест", "labor": 777, "material": 0})
    found = client.get("/api/catalog", params={"q": "Унікальна авторська робота"}).json()
    assert found["items"][0]["labor"] == 777

    obj = client.post("/api/objects", json={"name": "О", "city": "Полтава"}).json()
    added = client.post(f"/api/objects/{obj['id']}/positions",
                        json={"name": "Унікальна авторська робота", "unit": "м2",
                              "quantity": 2}).json()
    assert added["labor_price"] == pytest.approx(777 * 1.05)   # коефіцієнт Полтави
