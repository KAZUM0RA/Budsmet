"""Парсер має точно розбирати реальні відомості обсягів робіт АВК-5."""
import pytest

from backend.services.importer import parse_document


@pytest.fixture(scope="module")
def doc_a(pytestconfig):
    from tests.conftest import SAMPLES
    return parse_document("vob.pdf", (SAMPLES / "vob_118_semenivka.pdf").read_bytes())


@pytest.fixture(scope="module")
def doc_b(pytestconfig):
    from tests.conftest import SAMPLES
    return parse_document("vob.pdf", (SAMPLES / "vob_174_lokhvytsia.pdf").read_bytes())


def test_all_positions_are_parsed(doc_a, doc_b):
    assert len(doc_a.positions) == 116
    assert len(doc_b.positions) == 129
    assert not doc_a.warnings
    assert not doc_b.warnings


def test_position_numbers_are_continuous(doc_a, doc_b):
    for doc, last in ((doc_a, 116), (doc_b, 129)):
        numbers = [p.number for p in doc.positions]
        assert numbers == list(range(1, last + 1))


def test_object_header_is_extracted(doc_a, doc_b):
    assert "Капітальний ремонт" in doc_a.object_name
    assert doc_a.city == "Семенівка"
    assert doc_a.region == "Полтавська область"
    assert doc_a.doc_code == "118_ДЦ_ВОБ"

    assert doc_b.city == "Лохвиця"
    assert doc_b.region == "Полтавська область"
    assert doc_b.doc_code == "174_ДЦ_ВОБ"
    assert "Героїв України" in doc_b.address


def test_structure_of_estimates_and_divisions(doc_a):
    estimates = [s for s in doc_a.sections if s.kind == "estimate"]
    divisions = [s for s in doc_a.sections if s.kind == "division"]
    assert len(estimates) == 5
    assert estimates[0].code == "02-01-01_П-АБ.АР"
    assert estimates[0].title == "Загальнобудівельні роботи"
    assert divisions[0].title == "Демонтажні роботи"


def test_known_positions_have_right_unit_and_quantity(doc_a):
    by_number = {p.number: p for p in doc_a.positions}
    assert by_number[1].unit == "шт" and by_number[1].quantity == 4
    assert by_number[2].unit == "м2" and by_number[2].quantity == 7.36
    assert by_number[61].unit == "т" and by_number[61].quantity == 9.690009
    assert by_number[116].unit == "компл" and by_number[116].quantity == 1
    # Багаторядкова назва склеюється в один рядок.
    assert by_number[1].name == ("Демонтаж дверних коробок в кам'яних стінах "
                                 "з відбиванням штукатурки в укосах")


def test_multiline_wrapped_names_keep_full_text(doc_b):
    by_number = {p.number: p for p in doc_b.positions}
    assert by_number[15].unit == "грати"
    assert "понад 5 до 6, 5 м2" in by_number[15].name


def test_csv_import():
    csv = ("№;Найменування робіт;Одиниця виміру;Кількість\n"
           "1;Демонтаж розеток;шт;24\n"
           "2;Улаштування бетонної стяжки товщиною 20 мм площею до 20 м2;м2;18,5\n").encode()
    doc = parse_document("obsyahy.csv", csv)
    assert len(doc.positions) == 2
    assert doc.positions[1].unit == "м2"
    assert doc.positions[1].quantity == 18.5
