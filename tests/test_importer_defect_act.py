"""Дефектний акт АВК-5: інша структура, ніж у відомості обсягів робіт.

Між позиціями тут вклинюються підзаголовки ділянок («ФАСАД», «СХОДИ») і
рядки розрахунків («ФЕМ-130кг*4,2м2=546кг») — вони не мають ні губити
позиції, ні ставати позиціями самі.
"""
import pytest

from backend.services.importer import parse_document
from tests.conftest import SAMPLES

ACT = SAMPLES / "defektnyi_akt_41_myrhorod.pdf"


@pytest.fixture(scope="module")
def act():
    return parse_document("akt.pdf", ACT.read_bytes())


def test_every_position_is_recognised(act):
    assert len(act.positions) == 47
    assert [p.number for p in act.positions] == list(range(1, 48))
    assert not act.warnings


def test_header_is_extracted(act):
    assert act.object_name.startswith("Капітальний ремонт нежитлового приміщення")
    # Службовий префікс «на капітальний ремонт» не дублюється в назві.
    assert not act.object_name.lower().startswith("на ")
    assert "вул. Гоголя, 88" in act.address
    assert act.city == "Миргород"
    assert act.region == "Полтавська область"
    assert act.doc_code == "41_ДЦ_ДФ_02-01-01"


def test_units_and_quantities(act):
    by_number = {p.number: p for p in act.positions}
    assert (by_number[1].unit, by_number[1].quantity) == ("м3", 1.6)
    assert (by_number[3].unit, by_number[3].quantity) == ("м2", 19.7)
    assert (by_number[13].unit, by_number[13].quantity) == ("агрегат", 2)
    assert (by_number[23].unit, by_number[23].quantity) == ("компл", 2)
    assert (by_number[24].unit, by_number[24].quantity) == ("місць", 1)
    assert (by_number[40].unit, by_number[40].quantity) == ("т", 0.945)
    assert (by_number[46].unit, by_number[46].quantity) == ("т", 11.731352)
    # Одиниця виміру завжди береться з колонки, а не вгадується з назви.
    assert all(p.unit for p in act.positions)


def test_positions_followed_by_notes_survive(act):
    """Рядки розрахунків після позиції не мають з'їдати її одиницю й обсяг."""
    by_number = {p.number: p for p in act.positions}
    # Після 5-ї йдуть два рядки розрахунку, після 9-ї — один.
    assert (by_number[5].unit, by_number[5].quantity) == ("м2", 4.2)
    assert (by_number[9].unit, by_number[9].quantity) == ("м2", 72)
    assert "ФЕМ" in by_number[5].name
    assert "546" not in by_number[5].name      # розрахунок не втрапив у назву


def test_calculation_lines_never_become_positions(act):
    for position in act.positions:
        assert "=" not in position.name
        assert "*" not in position.name


def test_site_subheaders_become_sections(act):
    titles = [s.title for s in act.sections]
    assert "ФАСАД" in titles
    assert "КАСОВИЙ ВУЗОЛ" in titles
    assert "ЕЛЕКТРОМОНТАЖНІ РОБОТИ" in titles
    # Підзаголовок не став позицією.
    assert all(p.name != "ФАСАД" for p in act.positions)


def test_numbered_divisions_with_number_sign(act):
    """У актах розділ пишуть як «Розділ №1.» — зі знаком номера."""
    titles = [s.title for s in act.sections]
    assert "Демонтажні роботи" in titles
    assert "Монтажні роботи" in titles
    assert "Інші роботи" in titles
