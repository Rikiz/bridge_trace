"""SQLite-backed group manager for binding paths into logical groups."""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from bridgetrace.config import settings
from bridgetrace.models.group import Group

logger = logging.getLogger(__name__)

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS groups (
    name       TEXT PRIMARY KEY,
    paths      TEXT NOT NULL DEFAULT '[]',
    metadata   TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
"""


class GroupManager:
    """Manage logical groups stored in a local SQLite database."""

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or settings.sqlite_path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_TABLE_SQL)
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()

    def __enter__(self) -> GroupManager:
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def add(self, name: str, paths: list[str], metadata: dict[str, str] | None = None) -> Group:
        """Add or update a group with the given paths."""
        now = datetime.now().isoformat()
        meta = metadata or {}
        self._conn.execute(
            """
            INSERT INTO groups (name, paths, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                paths = excluded.paths,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (name, json.dumps(paths), json.dumps(meta), now, now),
        )
        self._conn.commit()
        logger.info("Group '%s' saved with %d paths", name, len(paths))
        return Group(name=name, paths=paths, metadata=meta)

    def get(self, name: str) -> Group | None:
        """Retrieve a group by name."""
        row = self._conn.execute(
            "SELECT name, paths, metadata, created_at, updated_at FROM groups WHERE name = ?",
            (name,),
        ).fetchone()
        if row is None:
            return None
        return Group(
            name=row[0],
            paths=json.loads(row[1]),
            metadata=json.loads(row[2]),
            created_at=datetime.fromisoformat(row[3]),
            updated_at=datetime.fromisoformat(row[4]),
        )

    def list_groups(self) -> list[Group]:
        """Return all groups."""
        rows = self._conn.execute(
            "SELECT name, paths, metadata, created_at, updated_at FROM groups"
        ).fetchall()
        return [
            Group(
                name=r[0],
                paths=json.loads(r[1]),
                metadata=json.loads(r[2]),
                created_at=datetime.fromisoformat(r[3]),
                updated_at=datetime.fromisoformat(r[4]),
            )
            for r in rows
        ]

    def remove(self, name: str) -> bool:
        """Delete a group by name. Returns True if deleted."""
        cursor = self._conn.execute("DELETE FROM groups WHERE name = ?", (name,))
        self._conn.commit()
        deleted = cursor.rowcount > 0
        if deleted:
            logger.info("Group '%s' removed", name)
        return deleted
