"""Запис вручну змінених цін у довідник розцінок."""
import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services import catalog


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def make_object(client, city="Полтава"):
    return client.post("/api/objects", json={"name": "Об'єкт", "city": city}).json()["id"]


def add_position(client, object_id, name, unit="м2", quantity=10):
    return client.post(f"/api/objects/{object_id}/positions",
                       json={"name": name, "unit": unit, "quantity": quantity}).json()


def test_manual_price_updates_matched_catalog_item(client, clean_db):
    oid = make_object(client)
    pos = add_position(client, oid, "Демонтаж розеток", "шт")
    code = pos["match_code"]
    assert code

    client.patch(f"/api/positions/{pos['id']}", json={"labor_price": 210})
    result = client.post(f"/api/positions/{pos['id']}/to-catalog",
                         json={"mode": "auto"}).json()

    assert result["created"] is False
    assert result["item"]["code"] == code            # оновлено ту саму розцінку
    assert catalog.by_code(code)["labor"] == pytest.approx(210 / 1.05, rel=1e-3)


def test_regional_coefficient_is_not_applied_twice(client, clean_db):
    """Ціна в довіднику базова; повторний запис не має її роздувати."""
    oid = make_object(client, city="Київ")               # коефіцієнт 1.35
    pos = add_position(client, oid, "Демонтаж розеток", "шт")

    client.patch(f"/api/positions/{pos['id']}", json={"labor_price": 270})
    client.post(f"/api/positions/{pos['id']}/to-catalog", json={"mode": "auto"})
    base_after_first = catalog.by_code(pos["match_code"])["labor"]
    assert base_after_first == pytest.approx(200.0, rel=1e-3)

    # У кошторисі знову має вийти 270, а не 270 × 1.35.
    client.post(f"/api/objects/{oid}/match-catalog", json={"keep_manual": False})
    calc = client.get(f"/api/objects/{oid}").json()
    position = calc["estimates"][0]["divisions"][0]["positions"][0]
    assert position["labor_price"] == pytest.approx(270.0, rel=1e-3)

    # І повторний запис не змінює базову ціну.
    client.post(f"/api/positions/{position['id']}/to-catalog", json={"mode": "auto"})
    assert catalog.by_code(pos["match_code"])["labor"] == pytest.approx(200.0, rel=1e-3)


def test_unknown_work_becomes_new_catalog_entry(client, clean_db):
    oid = make_object(client)
    pos = client.post(f"/api/objects/{oid}/positions", json={
        "name": "Полірування вітражів ручної роботи", "unit": "м2",
        "quantity": 5, "labor_price": 840, "manual": True}).json()
    assert pos["price_source"] == "manual"

    result = client.post(f"/api/positions/{pos['id']}/to-catalog",
                         json={"mode": "auto", "category": "Мої розцінки"}).json()
    assert result["created"] is True
    item = result["item"]
    assert item["name"] == "Полірування вітражів ручної роботи"
    assert item["unit"] == "м2"
    assert item["category"] == "Мої розцінки"
    assert item["labor"] == pytest.approx(840 / 1.05, rel=1e-3)

    # Нову розцінку одразу видно в довіднику й у пошуку.
    found = client.get("/api/catalog", params={"q": "Полірування вітражів"}).json()
    assert any(i["code"] == item["code"] for i in found["items"])


def test_new_entry_is_used_by_next_object(client, clean_db):
    """Сенс запису: наступний кошторис бере вже вашу ціну."""
    first = make_object(client)
    pos = client.post(f"/api/objects/{first}/positions", json={
        "name": "Монтаж декоративного каміння", "unit": "м2",
        "quantity": 3, "labor_price": 630, "manual": True}).json()
    client.post(f"/api/positions/{pos['id']}/to-catalog", json={"mode": "auto"})

    second = make_object(client)
    added = add_position(client, second, "Монтаж декоративного каміння", "м2")
    assert added["price_source"] == "catalog"
    assert added["labor_price"] == pytest.approx(630.0, rel=1e-3)


def test_bulk_writes_only_manual_positions(client, clean_db):
    oid = make_object(client)
    auto = add_position(client, oid, "Демонтаж розеток", "шт")
    manual = add_position(client, oid, "Знімання наличників", "м")
    client.patch(f"/api/positions/{manual['id']}", json={"labor_price": 99})

    stats = client.post(f"/api/objects/{oid}/manual-to-catalog",
                        json={}).json()["stats"]
    assert stats["updated"] + stats["created"] == 1
    assert catalog.by_code(manual["match_code"])["labor"] == pytest.approx(99 / 1.05, rel=1e-3)
    # Автоматично оцінена позиція лишилась як була.
    assert catalog.by_code(auto["match_code"])["labor"] == 55.0


def test_update_mode_requires_a_matched_item(client, clean_db):
    oid = make_object(client)
    pos = client.post(f"/api/objects/{oid}/positions", json={
        "name": "Абсолютно унікальна робота без аналогів", "unit": "шт",
        "quantity": 1, "labor_price": 100, "manual": True}).json()
    response = client.post(f"/api/positions/{pos['id']}/to-catalog",
                           json={"mode": "update"})
    assert response.status_code == 400
    assert "створіть нову" in response.json()["detail"]


def test_position_without_name_is_rejected(client, clean_db):
    oid = make_object(client)
    pos = add_position(client, oid, "Демонтаж розеток", "шт")
    from backend import db
    db.execute("UPDATE positions SET name='' WHERE id=?", (pos["id"],))
    response = client.post(f"/api/positions/{pos['id']}/to-catalog", json={})
    assert response.status_code == 400


def test_repeated_bulk_write_reports_nothing_new(client, clean_db):
    """Другий запис тих самих цін не має вдавати, що щось змінилось."""
    oid = make_object(client)
    pos = add_position(client, oid, "Знімання наличників", "м")
    client.patch(f"/api/positions/{pos['id']}", json={"labor_price": 45})

    first = client.post(f"/api/objects/{oid}/manual-to-catalog", json={}).json()["stats"]
    assert first["updated"] + first["created"] == 1
    assert first["unchanged"] == 0

    second = client.post(f"/api/objects/{oid}/manual-to-catalog", json={}).json()["stats"]
    assert second["updated"] + second["created"] == 0
    assert second["unchanged"] == 1

    # Змінили ціну — знову є що записувати.
    client.patch(f"/api/positions/{pos['id']}", json={"labor_price": 60})
    third = client.post(f"/api/objects/{oid}/manual-to-catalog", json={}).json()["stats"]
    assert third["updated"] == 1
    assert catalog.by_code(pos["match_code"])["labor"] == pytest.approx(60 / 1.05, rel=1e-3)
