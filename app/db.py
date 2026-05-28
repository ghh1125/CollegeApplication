"""SQLite database connection helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_ROOT / "data" / "college.db"


@contextmanager
def get_conn() -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection and manage transaction finalization."""

    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
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

