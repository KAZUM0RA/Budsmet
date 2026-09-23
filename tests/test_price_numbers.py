"""Розбір сум у гривнях — місце, де помилка коштує найдорожче."""
import pytest

from backend.services.price_sites import _price_from
from backend.services.web_prices import extract_prices

# Ціни в прайсах пишуть і з роздільником тисяч, і без нього.
THOUSANDS = [
    ("2400", 2400.0),
    ("12500", 12500.0),
    ("1850 грн", 1850.0),
    ("1 250 грн", 1250.0),
    ("1 250,50", 1250.5),
    ("від 95 грн", 95.0),
    ("450", 450.0),
    ("35,50", 35.5),
]


@pytest.mark.parametrize("text,expected", THOUSANDS)
def test_price_cell_keeps_full_number(text, expected):
    """«2400» не має перетворюватись на «240»."""
    assert _price_from(text) == expected


@pytest.mark.parametrize("text,expected", [
    ("Монтаж перил — 2600 грн/м", 2600.0),
    ("Укладання плитки 1500 грн/м2", 1500.0),
    ("Робота 12500 грн/т", 12500.0),
    ("Ціна 450 грн/м2", 450.0),
    ("Від 1 250 грн за м2", 1250.0),
])
def test_web_snippet_keeps_full_number(text, expected):
    """Та сама помилка в пошуку давала б ціни в рази менші за справжні."""
    values = [s.value for s in extract_prices(text, "")]
    assert expected in values, f"«{text}» → {values}"


def test_range_takes_the_middle():
    assert _price_from("480-520 грн") == 500.0
    assert 350.0 in [s.value for s in extract_prices("Ціна 300-400 грн/шт", "")]


def test_long_text_is_not_a_price_cell():
    """Довгий текст містить сторонні числа і ціною бути не може."""
    assert _price_from("Фарбування стін за 2 рази валиком по підготовленій поверхні") is None
