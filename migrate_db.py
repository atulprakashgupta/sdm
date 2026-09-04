"""Idempotent migration for existing SQLite development databases.

Adds the password_reset_tokens table (needed for the forgot-password feature)
to a database created before this table existed.

Usage:
    python migrate_db.py            # migrates instance/sdm.sqlite
    python migrate_db.py <path>     # migrates a specific SQLite file
"""
import sqlite3
import sys
from pathlib import Path

DEFAULT_PATH = Path("instance/sdm.sqlite")

MIGRATIONS = [
    """
    CREATE TABLE IF NOT EXISTS password_reset_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        expires_at TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_reset_tokens_user ON password_reset_tokens (user_id)",
]


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    if not path.exists():
        print(f"No database found at {path}. Nothing to migrate.")
        return

    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        for statement in MIGRATIONS:
            connection.execute(statement)
        connection.commit()

        tables = [row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")]
        print(f"Migrated {path}. Tables now present:")
        for table in tables:
            print(f"  - {table}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
