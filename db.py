"""SQLite database connection helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / "data" / "college.db"


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection and manage transaction finalization.

    Opens read-only when the DB already exists (safe on read-only filesystems
    like Streamlit Cloud's /mount/src/). Falls back to read-write when the DB
    is absent so tests and local init scripts can still create it.
    """
    if DB_PATH.exists():
        uri = f"file:{DB_PATH}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    else:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        yield conn
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_cursor(conn: sqlite3.Connection) -> Iterator[sqlite3.Cursor]:
    """Yield a cursor and close it when the caller leaves the context."""

    cursor = conn.cursor()
    try:
        yield cursor
    finally:
        cursor.close()

