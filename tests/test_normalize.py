from backend.services.normalize import (clean_text, normalize_unit, parse_number,
                                        soft_clean, stem_phrase)


def test_units_are_canonicalised():
    assert normalize_unit("кв.м") == "м2"
    assert normalize_unit("м²") == "м2"
    assert normalize_unit("к-т") == "компл"
    assert normalize_unit("комплект") == "компл"
    assert normalize_unit("пог.м") == "м"
    assert normalize_unit("вимір.") == "вимір"


def test_numbers_in_ukrainian_format():
    assert parse_number("9,690009") == 9.690009
    assert parse_number("1 234,56") == 1234.56
    assert parse_number("13,84287") == 13.84287
    assert parse_number("") is None
    assert parse_number("не число") is None


def test_apostrophes_and_spaces_are_unified():
    assert clean_text("кам’яних   стінах") == "кам'яних стінах"
    assert soft_clean("  шт 4") == "  шт 4"          # подвійні пробіли зберігаються
    assert clean_text("  шт   4") == "шт 4"


def test_number_sign_is_not_mangled():
    # NFKC перетворив би № на "No" — для шапки об'єкта це неприйнятно.
    assert "№" in clean_text("ТВБВ № 10016/016")


def test_stemming_groups_word_forms():
    assert stem_phrase("Демонтаж розеток").startswith("демонтаж")
    assert stem_phrase("Улаштування стяжки") == stem_phrase("улаштування стяжки")


def test_spaced_and_dotted_unit_forms():
    """Прайси пишуть одиниці як завгодно: «кв. м», «пог. м», «куб.м»."""
    for raw in ("кв.м", "кв м", "кв. м", "м.кв", "м кв", "КВ.М", "м²"):
        assert normalize_unit(raw) == "м2", raw
    for raw in ("куб.м", "куб. м", "м.куб", "м³"):
        assert normalize_unit(raw) == "м3", raw
    for raw in ("пог.м", "пог. м", "п.м", "м.п", "мп"):
        assert normalize_unit(raw) == "м", raw
