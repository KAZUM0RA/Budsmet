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
