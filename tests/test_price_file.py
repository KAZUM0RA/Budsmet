"""Завантаження прайс-листа файлом — коли сайт не піддається розбору."""
import io

import pytest
from fastapi.testclient import TestClient

from backend.main import app
from backend.services.price_sites import parse_price_file

XLSX_ROWS = [
    ["Прайс-лист на ремонтні роботи", "", ""],
    ["Демонтажні роботи", "", ""],
    ["Найменування роботи", "Од. вим.", "Ціна, грн"],
    ["Розбирання кам'яної кладки простих стін із цегли", "м3", "1 850 грн"],
    ["Розбирання монолітних бетонних сходів", "м3", "2400"],
    ["Оздоблювальні роботи", "", ""],
    ["Улаштування металевих нержавіючих перил", "м.п", "2 600 грн"],
    ["Установлення змішувачів", "шт", "450"],
]


def make_xlsx() -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    for row in XLSX_ROWS:
        sheet.append(row)
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_xlsx_price_list_is_parsed():
    prices = {p.name: p for p in parse_price_file("prais.xlsx", make_xlsx())}
    assert prices["Розбирання монолітних бетонних сходів"].price == 2400.0
    assert prices["Розбирання кам'яної кладки простих стін із цегли"].unit == "м3"
    assert prices["Улаштування металевих нержавіючих перил"].unit == "м"      # «м.п»
    assert prices["Установлення змішувачів"].category == "Оздоблювальні роботи"


def test_csv_price_list_is_parsed():
    csv = ("Найменування;Одиниця виміру;Ціна\n"
           "Штроблення стін під проводку;м;190 грн\n"
           "Монтаж гіпсокартону;кв.м;420\n").encode()
    prices = {p.name: p for p in parse_price_file("prais.csv", csv)}
    assert prices["Монтаж гіпсокартону"].price == 420.0
    assert prices["Монтаж гіпсокартону"].unit == "м2"


def test_saved_html_page_is_parsed():
    """Сторінку, збережену з браузера, розбираємо так само."""
    html = """<html><body><h2>Малярні роботи</h2>
      <div class="row"><span>Шпаклювання стін</span><span>м2</span><span>180 грн</span></div>
      <div class="row"><span>Фарбування стін</span><span>м2</span><span>2400 грн</span></div>
      </body></html>""".encode()
    prices = {p.name: p for p in parse_price_file("saved.html", html)}
    assert prices["Фарбування стін"].price == 2400.0
    assert prices["Шпаклювання стін"].category == "Малярні роботи"


def test_upload_endpoint_stores_prices(client, clean_db):
    response = client.post("/api/price-site/upload",
                           files={"file": ("prais.xlsx", io.BytesIO(make_xlsx()),
                                           "application/vnd.openxmlformats-officedocument"
                                           ".spreadsheetml.sheet")})
    assert response.status_code == 200
    body = response.json()
    assert body["saved"] == 4
    assert body["source"].startswith("файл:")

    listed = client.get("/api/price-site").json()
    assert listed["count"] == 4
    assert any(s["site"].startswith("файл:") for s in listed["sources"])


def test_upload_rejects_file_without_prices(client, clean_db):
    response = client.post("/api/price-site/upload",
                           files={"file": ("text.csv", io.BytesIO(b"just text\nno prices\n"),
                                           "text/csv")})
    assert response.status_code == 422


def test_uploaded_prices_are_used_for_estimates(client, clean_db, monkeypatch):
    from backend import config
    monkeypatch.setattr(config, "PRICE_SITE", "файл: prais.xlsx")
    client.post("/api/price-site/upload",
                files={"file": ("prais.xlsx", io.BytesIO(make_xlsx()), "application/x")})

    oid = client.post("/api/objects", json={"name": "О", "city": "Полтава"}).json()["id"]
    added = client.post(f"/api/objects/{oid}/positions", json={
        "name": "Улаштування металевих нержавіючих перил висотою 900 мм",
        "unit": "м", "quantity": 12}).json()
    assert added["price_source"] == "site"
    assert added["labor_price"] == pytest.approx(2600 * 1.05, rel=1e-3)
