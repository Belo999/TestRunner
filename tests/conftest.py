from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture
def tmp_db(tmp_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Create a temporary SQLite database with the project schema."""
    db_path = tmp_path / "test.db"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    schema = (Path(__file__).resolve().parents[1] / "apps" / "api" / "database.py").read_text()
    # Extract SCHEMA_SQLITE from the file
    import re
    match = re.search(r'SCHEMA_SQLITE\s*=\s"""(.*?)"""', schema, re.DOTALL)
    if match:
        conn.executescript(match.group(1))

    yield conn
    conn.close()


@pytest.fixture
def tmp_result_dir(tmp_path: Path) -> Path:
    """Create a temporary directory for engine results."""
    result_dir = tmp_path / "results"
    result_dir.mkdir()
    return result_dir


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure tests don't leak env vars."""
    monkeypatch.delenv("MARATHONRUNNER_DB_PATH", raising=False)
    monkeypatch.delenv("MARATHONRUNNER_DB_BACKEND", raising=False)
    monkeypatch.delenv("REDIS_HOST", raising=False)
    monkeypatch.delenv("MARATHONRUNNER_REDIS_ENABLED", raising=False)
    monkeypatch.delenv("OBJECT_STORAGE_ENDPOINT", raising=False)
    monkeypatch.delenv("MARATHONRUNNER_OBJECT_STORAGE_ENABLED", raising=False)
