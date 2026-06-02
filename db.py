"""SQLite database connection helpers."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent

# Default province — override via get_conn(province=...) for multi-province support.
_DEFAULT_PROVINCE = "zhejiang"

# Legacy path kept for backwards compatibility with tests that patch DB_PATH directly.
DB_PATH = PROJECT_ROOT / "data" / "zhejiang" / "college.db"


def get_db_path(province: str = _DEFAULT_PROVINCE) -> Path:
    """Return the SQLite DB path for the given province slug (e.g. 'zhejiang')."""
    return PROJECT_ROOT / "data" / province / "college.db"


@contextmanager
def get_conn(province: str = _DEFAULT_PROVINCE) -> Iterator[sqlite3.Connection]:
    """Yield a SQLite connection for the given province.

    Opens read-only when the DB already exists (safe on read-only filesystems
    like Streamlit Cloud's /mount/src/). Falls back to read-write when the DB
    is absent so tests and local init scripts can still create it.
    """
    db_path = get_db_path(province)
    if db_path.exists():
        uri = f"file:{db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
    else:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
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
