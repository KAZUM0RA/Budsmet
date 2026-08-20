import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Кожен запуск тестів працює з окремою БД, щоб не чіпати робочу.
_TMP_DB = Path(tempfile.mkdtemp(prefix="budsmet-test-")) / "test.db"
os.environ["BUDSMET_DB"] = str(_TMP_DB)

import pytest  # noqa: E402

from backend import config, db  # noqa: E402
from backend.services import catalog, price_sites  # noqa: E402

# Жоден тест не має звертатись до справжнього сайту прайса.
config.PRICE_SITE = ""

SAMPLES = ROOT / "samples"


@pytest.fixture(scope="session", autouse=True)
def _database():
    db.init_db()
    catalog.invalidate()
    yield


@pytest.fixture()
def clean_db():
    """Порожні таблиці перед тестом, що пише в БД."""
    with db.transaction() as conn:
        for table in ("positions", "divisions", "estimates", "objects",
                      "price_history", "web_price_cache", "catalog_overrides",
                      "site_prices"):
            conn.execute(f"DELETE FROM {table}")
    catalog.invalidate()
    price_sites.invalidate()
    yield


@pytest.fixture(scope="session")
def vob_semenivka() -> bytes:
    return (SAMPLES / "vob_118_semenivka.pdf").read_bytes()


@pytest.fixture(scope="session")
def vob_lokhvytsia() -> bytes:
    return (SAMPLES / "vob_174_lokhvytsia.pdf").read_bytes()
