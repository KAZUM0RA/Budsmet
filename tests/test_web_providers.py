"""Розбір відповідей пошукових сервісів та ощадливість запитів.

Провайдери викликаються з підставленими відповідями у форматі, який реально
повертають SerpAPI, Brave, Google Custom Search і власний ендпоінт.
"""
import pytest

from backend import config
from backend.services import pricing, web_prices
from backend.services.web_prices import WebBudget

SERPAPI_RESPONSE = {
    "organic_results": [
        {"title": "Укладання плитки Полтава", "snippet": "Ціна робіт від 520 грн/м2 під ключ",
         "link": "https://example.ua/1"},
        {"title": "Плиточні роботи", "snippet": "вартість робіт 480 грн за м2",
         "link": "https://example.ua/2"},
    ]
}
BRAVE_RESPONSE = {
    "web": {"results": [
        {"title": "Монтаж плитки", "description": "роботи 500 грн/м2", "url": "https://example.ua/3"},
    ]}
}
GOOGLE_RESPONSE = {
    "items": [
        {"title": "Плитка ціна", "snippet": "укладання 540 грн/м2", "link": "https://example.ua/4"},
    ]
}
CUSTOM_RESPONSE = {
    "results": [
        {"title": "Свій прайс", "snippet": "роботи 510 грн/м2", "url": "https://example.ua/5"},
    ]
}


@pytest.fixture()
def fake_http(monkeypatch):
    """Підміняє мережевий виклик; запам'ятовує, з якими параметрами звертались."""
    calls = []

    def make(response):
        def _http_get(url, params, headers=None):
            calls.append({"url": url, "params": params, "headers": headers or {}})
            return response
        monkeypatch.setattr(web_prices, "_http_get", _http_get)
        return calls

    return make


def test_serpapi_response_is_mapped(fake_http, monkeypatch):
    monkeypatch.setattr(config, "SERPAPI_KEY", "test-key")
    calls = fake_http(SERPAPI_RESPONSE)
    results = web_prices._provider_serpapi("укладання плитки ціна Полтава")
    assert len(results) == 2
    assert results[0]["title"] == "Укладання плитки Полтава"
    assert results[0]["snippet"].startswith("Ціна робіт")
    assert results[0]["url"] == "https://example.ua/1"
    assert calls[0]["params"]["api_key"] == "test-key"
    assert calls[0]["params"]["gl"] == "ua"      # регіон пошуку — Україна


def test_brave_response_is_mapped(fake_http, monkeypatch):
    monkeypatch.setattr(config, "BRAVE_API_KEY", "brave-key")
    calls = fake_http(BRAVE_RESPONSE)
    results = web_prices._provider_brave("монтаж плитки")
    assert results == [{"title": "Монтаж плитки", "snippet": "роботи 500 грн/м2",
                        "url": "https://example.ua/3"}]
    # Ключ Brave передається заголовком, а не параметром запиту.
    assert calls[0]["headers"]["X-Subscription-Token"] == "brave-key"


def test_google_response_is_mapped(fake_http, monkeypatch):
    monkeypatch.setattr(config, "GOOGLE_CSE_KEY", "g-key")
    monkeypatch.setattr(config, "GOOGLE_CSE_CX", "g-cx")
    calls = fake_http(GOOGLE_RESPONSE)
    results = web_prices._provider_google("плитка")
    assert results[0]["url"] == "https://example.ua/4"
    assert calls[0]["params"]["cx"] == "g-cx"


def test_custom_endpoint_is_mapped(fake_http, monkeypatch):
    monkeypatch.setattr(config, "CUSTOM_PRICE_ENDPOINT", "https://my.endpoint/search")
    calls = fake_http(CUSTOM_RESPONSE)
    results = web_prices._provider_custom("плитка")
    assert results[0]["title"] == "Свій прайс"
    assert calls[0]["url"] == "https://my.endpoint/search"


def test_empty_response_does_not_crash(fake_http, monkeypatch):
    monkeypatch.setattr(config, "SERPAPI_KEY", "k")
    fake_http({})
    assert web_prices._provider_serpapi("що завгодно") == []


@pytest.fixture()
def serpapi_enabled(monkeypatch, clean_db):
    """Вмикає провайдера serpapi з підставленою мережею."""
    monkeypatch.setattr(config, "PRICE_PROVIDER", "serpapi")
    monkeypatch.setattr(config, "SERPAPI_KEY", "test-key")
    counter = {"requests": 0}

    def _http_get(url, params, headers=None):
        counter["requests"] += 1
        return SERPAPI_RESPONSE

    monkeypatch.setattr(web_prices, "_http_get", _http_get)
    return counter


def test_lookup_returns_median_price(serpapi_enabled):
    price = web_prices.lookup("Укладання плитки", "м2", "Полтава")
    assert price.found
    assert price.provider == "serpapi"
    assert 480 <= price.labor <= 520     # медіана з 520 і 480
    assert price.samples


def test_second_lookup_comes_from_cache(serpapi_enabled):
    first = web_prices.lookup("Укладання плитки", "м2", "Полтава")
    assert serpapi_enabled["requests"] == 1
    second = web_prices.lookup("Укладання плитки", "м2", "Полтава")
    assert second.cached is True
    assert second.labor == first.labor
    assert serpapi_enabled["requests"] == 1    # мережу вдруге не смикали


def test_budget_limits_live_queries(serpapi_enabled):
    budget = WebBudget(limit=2)
    for i in range(5):
        web_prices.lookup(f"Унікальна робота номер {i}", "м2", "Полтава", budget=budget)
    assert budget.used == 2
    assert budget.blocked == 3
    assert serpapi_enabled["requests"] == 2    # ліміт справді стримав мережу


def test_cached_answers_do_not_spend_budget(serpapi_enabled):
    budget = WebBudget(limit=1)
    web_prices.lookup("Робота", "м2", "Полтава", budget=budget)
    web_prices.lookup("Робота", "м2", "Полтава", budget=budget)
    assert budget.used == 1
    assert budget.from_cache == 1
    assert budget.blocked == 0


def test_network_error_does_not_break_pricing(monkeypatch, clean_db):
    monkeypatch.setattr(config, "PRICE_PROVIDER", "serpapi")
    monkeypatch.setattr(config, "SERPAPI_KEY", "k")

    def boom(url, params, headers=None):
        raise RuntimeError("немає мережі")

    monkeypatch.setattr(web_prices, "_http_get", boom)
    # Ціна має відкотитись до довідника, а не впасти.
    res = pricing.resolve_price("Демонтаж розеток", "шт", "Полтава", "", strategy="web_first")
    assert res.source == "catalog"
    assert res.labor > 0


def test_web_fallback_skips_internet_for_known_works(serpapi_enabled):
    """Найощадніший режим: відома робота береться з довідника, мережа не чіпається."""
    res = pricing.resolve_price("Демонтаж розеток", "шт", "Полтава", "", strategy="web_fallback")
    assert res.source == "catalog"
    assert serpapi_enabled["requests"] == 0


def test_web_fallback_uses_internet_for_unknown_works(serpapi_enabled):
    res = pricing.resolve_price("Шліфування космічного трапа", "м2", "Полтава", "",
                                strategy="web_fallback")
    assert res.source == "web"
    assert serpapi_enabled["requests"] == 1
