"""SQLite-сховище: об'єкти, кошториси, позиції, історія цін, кеш інтернет-цін."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("BUDSMET_DB", Path(__file__).resolve().parent.parent / "budsmet.db"))

_local = threading.local()

SCHEMA = """
PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS objects (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    address      TEXT DEFAULT '',
    city         TEXT DEFAULT '',
    region       TEXT DEFAULT '',
    customer     TEXT DEFAULT '',
    doc_code     TEXT DEFAULT '',
    settings     TEXT DEFAULT '{}',
    created_at   TEXT DEFAULT (datetime('now')),
    updated_at   TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS estimates (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    object_id    INTEGER NOT NULL REFERENCES objects(id) ON DELETE CASCADE,
    code         TEXT DEFAULT '',
    title        TEXT DEFAULT '',
    ordinal      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS divisions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    estimate_id  INTEGER NOT NULL REFERENCES estimates(id) ON DELETE CASCADE,
    code         TEXT DEFAULT '',
    title        TEXT DEFAULT '',
    ordinal      INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS positions (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    division_id   INTEGER NOT NULL REFERENCES divisions(id) ON DELETE CASCADE,
    ordinal       INTEGER DEFAULT 0,
    number        INTEGER DEFAULT 0,
    name          TEXT NOT NULL,
    unit          TEXT DEFAULT '',
    quantity      REAL DEFAULT 0,
    labor_price   REAL DEFAULT 0,
    material_price REAL DEFAULT 0,
    machines_price REAL DEFAULT 0,
    price_source  TEXT DEFAULT '',
    match_code    TEXT DEFAULT '',
    match_score   REAL DEFAULT 0,
    manual        INTEGER DEFAULT 0,
    note          TEXT DEFAULT ''
);

-- Історія фактичних цін: наповнюється при збереженні кошторису.
CREATE TABLE IF NOT EXISTS price_history (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    work_key      TEXT NOT NULL,
    name          TEXT NOT NULL,
    unit          TEXT DEFAULT '',
    city          TEXT DEFAULT '',
    region        TEXT DEFAULT '',
    labor_price   REAL DEFAULT 0,
    material_price REAL DEFAULT 0,
    machines_price REAL DEFAULT 0,
    object_id     INTEGER,
    source        TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_history_key ON price_history(work_key, city);

-- Кеш відповідей інтернет-провайдерів цін.
CREATE TABLE IF NOT EXISTS web_price_cache (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    work_key      TEXT NOT NULL,
    city          TEXT DEFAULT '',
    unit          TEXT DEFAULT '',
    labor_price   REAL DEFAULT 0,
    material_price REAL DEFAULT 0,
    samples       TEXT DEFAULT '[]',
    provider      TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_webcache ON web_price_cache(work_key, city, provider);

-- Прайс-лист, завантажений із сайту (джерело цін «сайт»).
CREATE TABLE IF NOT EXISTS site_prices (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    site          TEXT NOT NULL,
    name          TEXT NOT NULL,
    unit          TEXT DEFAULT '',
    price         REAL DEFAULT 0,
    category      TEXT DEFAULT '',
    url           TEXT DEFAULT '',
    work_key      TEXT DEFAULT '',
    fetched_at    TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_site_prices ON site_prices(site, work_key);

-- Користувацькі розцінки, що доповнюють/перекривають JSON-довідник.
CREATE TABLE IF NOT EXISTS catalog_overrides (
    code          TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    unit          TEXT DEFAULT '',
    category      TEXT DEFAULT '',
    labor         REAL DEFAULT 0,
    material      REAL DEFAULT 0,
    machines      REAL DEFAULT 0,
    updated_at    TEXT DEFAULT (datetime('now'))
);
"""


def connect() -> sqlite3.Connection:
    """Одне з'єднання на потік (SQLite не любить ділити з'єднання між потоками)."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    conn.commit()


@contextmanager
def transaction():
    conn = connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def query(sql: str, params=()) -> list[sqlite3.Row]:
    return connect().execute(sql, params).fetchall()


def query_one(sql: str, params=()):
    return connect().execute(sql, params).fetchone()


def execute(sql: str, params=()) -> int:
    with transaction() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid


def row_to_dict(row) -> dict:
    return dict(row) if row is not None else None


def load_json_field(value, default):
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default
