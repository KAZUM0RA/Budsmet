"""Розбір прайс-листа сайту: різні верстки, сміття, підбір ціни."""
import pathlib

import pytest

from backend import config, db
from backend.services import price_sites, pricing
from backend.services.price_sites import _RowExtractor, parse_rows

FIXTURES = pathlib.Path(__file__).parent / "fixtures"


def parse_file(name: str):
    parser = _RowExtractor()
    parser.feed((FIXTURES / name).read_text(encoding="utf-8"))
    return parse_rows(parser.rows, url=f"https://example.ua/{name}")


def test_table_layout_is_parsed():
    prices = parse_file("price_table.html")
    by_name = {p.name: p for p in prices}
    assert "Шпаклювання стін під фарбування" in by_name
    item = by_name["Шпаклювання стін під фарбування"]
    assert item.unit == "м2"
    assert item.price == 180.0
    assert item.category == "Малярні роботи"


def test_price_forms_are_understood():
    by_name = {p.name: p for p in parse_file("price_table.html")}
    assert by_name["Фарбування стін водоемульсійною фарбою за 2 рази"].price == 120.0  # «від 120»
    assert by_name["Ґрунтування стін"].price == 30.0                                   # «25-35» → середина
    assert by_name["Укладання плитки на стіни"].price == 620.5                         # кома як роздільник
    assert by_name["Затирка швів"].price == 35.0                                       # символ ₴


def test_units_are_canonicalised():
    by_name = {p.name: p for p in parse_file("price_table.html")}
    assert by_name["Укладання керамічної плитки на підлогу"].unit == "м2"   # «кв.м»
    assert by_name["Укладання плитки на стіни"].unit == "м2"                # «кв. м»
    assert by_name["Затирка швів"].unit == "м"                              # «м.п»


def test_list_layout_is_parsed():
    by_name = {p.name: p for p in parse_file("price_table.html")}
    assert by_name["Установлення розетки"].unit == "шт"
    assert by_name["Установлення розетки"].price == 180.0
    assert by_name["Установлення розетки"].category == "Електромонтажні роботи"


def test_inline_text_layout_is_parsed():
    by_name = {p.name: p for p in parse_file("price_inline.html")}
    assert by_name["Демонтаж міжкімнатних дверей"].price == 250.0
    assert by_name["Розбирання плитки на підлозі"].unit == "м2"


def test_navigation_headers_and_scripts_are_ignored():
    names = [p.name for p in parse_file("price_table.html")]
    assert "Головна" not in names
    assert not any("Найменування" == n for n in names)     # шапка таблиці
    assert not any("грн" == n.strip() for n in names)
    assert not any("©" in n for n in names)                # підвал
    # Скрипт зі стилем не мають дати фальшивих позицій.
    assert all("var x" not in n for n in names)


def test_rows_without_price_are_skipped():
    names = [p.name for p in parse_file("price_inline.html")]
    assert "Просто текст без ціни та одиниці" not in names
    assert "Разом" not in names


def test_lookup_matches_stored_prices(clean_db, monkeypatch):
    site = "https://example.ua/price"
    monkeypatch.setattr(config, "PRICE_SITE", site)
    price_sites.invalidate()
    price_sites.save(site, parse_file("price_table.html"))

    hit = price_sites.lookup("Фарбування стін водоемульсійною фарбою", "м2", auto_refresh=False)
    assert hit is not None
    assert hit["labor"] == 120.0
    assert hit["material"] == 0.0        # прайс робіт — матеріали окремо
    assert hit["score"] >= 62


def test_lookup_returns_none_for_unrelated_work(clean_db, monkeypatch):
    site = "https://example.ua/price"
    monkeypatch.setattr(config, "PRICE_SITE", site)
    price_sites.invalidate()
    price_sites.save(site, parse_file("price_table.html"))
    assert price_sites.lookup("Монтаж турбіни реактивного двигуна", "шт",
                              auto_refresh=False) is None


def test_site_price_is_used_by_pricing_engine(clean_db, monkeypatch):
    site = "https://example.ua/price"
    monkeypatch.setattr(config, "PRICE_SITE", site)
    price_sites.invalidate()
    price_sites.save(site, parse_file("price_table.html"))

    res = pricing.resolve_price("Укладання керамічної плитки на підлогу", "м2",
                                "Полтава", "", strategy="site_first")
    assert res.source == "site"
    # 520 грн з прайса × коефіцієнт Полтави 1.05
    assert res.labor == pytest.approx(520 * 1.05, rel=1e-3)
    assert res.details["site"] == site


def test_history_still_beats_site(clean_db, monkeypatch):
    site = "https://example.ua/price"
    monkeypatch.setattr(config, "PRICE_SITE", site)
    price_sites.invalidate()
    price_sites.save(site, parse_file("price_table.html"))
    pricing.remember_price("Установлення розетки", "шт", "Полтава", "", 999.0, 0.0, 0.0)

    res = pricing.resolve_price("Установлення розетки", "шт", "Полтава", "",
                                strategy="site_first")
    assert res.source == "history"
    assert res.labor == 999.0


def test_refresh_reports_network_error(clean_db, monkeypatch):
    monkeypatch.setattr(config, "PRICE_SITE", "https://example.ua/price")

    def boom(url):
        raise RuntimeError("сайт недоступний")

    monkeypatch.setattr(price_sites, "_fetch", boom)
    result = price_sites.refresh(force=True)
    assert result["refreshed"] is False
    assert "сайт недоступний" in result["error"]


def test_refresh_reports_when_nothing_parsed(clean_db, monkeypatch):
    monkeypatch.setattr(config, "PRICE_SITE", "https://example.ua/price")
    monkeypatch.setattr(price_sites, "_fetch",
                        lambda url: "<html><body><p>Тут нема прайса</p></body></html>")
    result = price_sites.refresh(force=True)
    assert result["refreshed"] is False
    assert "не знайдено позицій" in result["error"]


def test_unreachable_site_is_retried_only_once(clean_db, monkeypatch):
    """Недоступний сайт не має спричиняти запит на кожній позиції кошторису."""
    monkeypatch.setattr(config, "PRICE_SITE", "https://example.ua/price")
    price_sites.invalidate()
    attempts = {"n": 0}

    def boom(url):
        attempts["n"] += 1
        raise RuntimeError("недоступно")

    monkeypatch.setattr(price_sites, "_fetch", boom)
    for _ in range(50):
        assert price_sites.lookup("Будь-яка робота", "м2") is None
    assert attempts["n"] == 1


def test_cooldown_expires(clean_db, monkeypatch):
    monkeypatch.setattr(config, "PRICE_SITE", "https://example.ua/price")
    price_sites.invalidate()
    monkeypatch.setattr(price_sites, "FAILED_RETRY_SECONDS", 0)
    attempts = {"n": 0}

    def boom(url):
        attempts["n"] += 1
        raise RuntimeError("недоступно")

    monkeypatch.setattr(price_sites, "_fetch", boom)
    price_sites.lookup("Робота", "м2")
    price_sites.lookup("Робота", "м2")
    assert attempts["n"] == 2
