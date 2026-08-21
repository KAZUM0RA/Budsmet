import pytest

from backend.services import catalog
from backend.services.matcher import WorkMatcher, work_key


def matcher() -> WorkMatcher:
    return WorkMatcher(catalog.items())


def test_exact_name_matches_perfectly():
    best = matcher().best("Демонтаж розеток", "шт")
    assert best is not None and best.score == 100.0
    assert best.name == "Демонтаж розеток"


def test_free_form_query_finds_the_right_work():
    best = matcher().best("фарбування стін водоемульсійною фарбою", "м2")
    assert best is not None
    assert "фарбування" in best.name.lower() and "стін" in best.name.lower()


def test_search_finds_long_names_by_single_word():
    names = [r.name.lower() for r in matcher().search("стяжка", limit=6)]
    assert any("стяжки" in n or "стяжок" in n for n in names)


def test_search_handles_ukrainian_inflection():
    # «розетка» → «розеток»: випадний голосний ламає простий пошук підрядка.
    names = [r.name for r in matcher().search("розетка", limit=6)]
    assert "Демонтаж розеток" in names
    assert any("штепсельних розеток" in n for n in names)


def test_search_returns_focused_result_set():
    # Пошук не має повертати пів довідника через випадкові збіги.
    assert len(matcher().search("плитка", limit=200)) < 40


def test_work_key_is_stable_across_word_forms_and_units():
    assert work_key("Демонтаж розеток", "шт") == work_key("демонтаж розеток", "штук")
    assert work_key("Улаштування стяжки", "м2") != work_key("Улаштування стяжки", "м3")


# ---------------------------------------------------- підбір за вільною назвою

# (як кошторисник написав, очікуване слово в назві розцінки)
LOOSE_NAMES = [
    ("Штукатурення стін", "м2", "штукатурення"),
    ("Поклейка шпалер", "м2", "шпалерами"),
    ("Зняття старих шпалер", "м2", "шпалер"),
    ("Заміна розеток", "шт", "розеток"),
    ("Монтаж підвісної стелі армстронг", "м2", "підвісної стелі"),
    ("Утеплення фасаду пінопластом", "м2", "фасаду"),
    ("Вивіз будівельного сміття", "т", "сміття"),
    ("Установка міжкімнатних дверей", "шт", "дверних"),
    ("Прокладка кабелю", "м", "кабелю"),
    ("Фарбування стелі водоемульсійкою", "м2", "фарбування"),
    ("Улаштування ламінату", "м2", "ламінату"),
    ("Розбирання дощатої підлоги", "м2", "дощатих"),
    ("Демонтаж старої плитки зі стін", "м2", "плиток"),
]


@pytest.mark.parametrize("query,unit,expected", LOOSE_NAMES)
def test_loose_names_are_matched(query, unit, expected):
    """Назви з відомостей рідко збігаються з довідником дослівно."""
    best = matcher().best(query, unit)
    assert best is not None, f"не підібрано розцінку для «{query}»"
    assert expected.lower() in best.name.lower(), \
        f"«{query}» → «{best.name}» (очікували згадку «{expected}»)"


# Сплутати демонтаж із монтажем — найдорожча помилка підбору.
DIFFERENT_ACTIONS = [
    ("Демонтаж розеток та вимикачів", "шт", "демонтаж"),
    ("Демонтаж плитки з підлоги", "м2", "розбирання"),
    ("Знімання шпалер зі стін", "м2", "знімання"),
    ("Улаштування покриття з плитки", "м2", "улаштування"),
    ("Монтаж розеток", "шт", "установлення"),
]


@pytest.mark.parametrize("query,unit,expected_start", DIFFERENT_ACTIONS)
def test_demolition_is_never_priced_as_installation(query, unit, expected_start):
    best = matcher().best(query, unit)
    assert best is not None, f"не підібрано розцінку для «{query}»"
    assert best.name.lower().startswith(expected_start.lower()), \
        f"«{query}» → «{best.name}»: клас робіт не збігається"


def test_distinguishing_word_outweighs_common_verb():
    """«Демонтаж» є в десятках розцінок — вирішувати має іменник."""
    best = matcher().best("Демонтаж світильників", "шт")
    assert best is not None and "світильник" in best.name.lower()


def test_unknown_words_do_not_block_the_match():
    """«Армстронг» немає в жодній розцінці — воно не має заважати підбору."""
    with_noise = matcher().best("Монтаж підвісної стелі армстронг суперфлекс", "м2")
    assert with_noise is not None
    assert "стел" in with_noise.name.lower()


def test_short_words_are_not_confused():
    """«Плитка» і «плита» — різні речі попри схожість написання."""
    best = matcher().best("Улаштування покриттів з керамічної плитки", "м2")
    assert best is not None
    assert "плиток" in best.name.lower() or "плитк" in best.name.lower()


def test_candidates_are_offered_even_below_threshold():
    """Для сумнівних назв програма має запропонувати варіанти на вибір."""
    found = matcher().candidates("Стяжка підлоги 5 см", "м2", limit=5)
    assert len(found) >= 3
    assert any("стяжк" in c.name.lower() for c in found)
    # Варіанти йдуть від найкращого до гіршого.
    assert found == sorted(found, key=lambda c: -c.score)
