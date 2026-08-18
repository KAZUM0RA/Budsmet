"""Розбір цін із текстових сніпетів пошуку та стан провайдера."""
from backend.services.web_prices import _aggregate, extract_prices, provider_status


def test_provider_is_off_by_default():
    status = provider_status()
    assert status["enabled"] is False
    assert "serpapi" in status["available"] and "brave" in status["available"]
    assert status["reason"]


def test_labor_price_is_recognised():
    samples = extract_prices("Укладання плитки на підлогу — ціна від 450 грн/м2, роботи під ключ", "м2")
    assert samples and samples[0].value == 450.0
    assert samples[0].kind == "labor"


def test_material_offer_is_classified_as_material():
    samples = extract_prices("Купити ламінат 32 клас, 899 грн за м.кв, в наявності, доставка", "м2")
    assert samples[0].kind == "material"


def test_price_range_becomes_midpoint():
    samples = extract_prices("Монтаж розетки 180-250 грн/шт", "шт")
    assert any(s.value == 215.0 for s in samples)


def test_prices_in_other_units_are_ignored():
    # Шукаємо ціну за м2, а в тексті — за погонний метр.
    assert extract_prices("Плінтус 120 грн/м.п", "м2") == []


def test_aggregate_uses_median_and_drops_outliers():
    from backend.services.web_prices import PriceSample
    values = [10, 400, 420, 430, 440, 450, 9000]
    samples = [PriceSample(float(v), "м2", "labor") for v in values]
    labor, material = _aggregate(samples)
    assert 400 <= labor <= 450
    assert material == 0.0


def test_disabled_provider_returns_empty_price(clean_db):
    from backend.services.web_prices import lookup
    price = lookup("Демонтаж розеток", "шт", "Полтава")
    assert price.found is False
