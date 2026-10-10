import os
import sqlite3
from contextlib import closing
from pathlib import Path

import psycopg

import auth_store


SOURCE_DATABASE = Path(__file__).resolve().parents[1] / "auth.sqlite3"


def migrate_users() -> tuple[int, int]:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Set DATABASE_URL to the managed PostgreSQL target before migrating.")
    if not SOURCE_DATABASE.is_file():
        raise FileNotFoundError(f"Local source database not found: {SOURCE_DATABASE.name}")

    source_uri = f"{SOURCE_DATABASE.as_uri()}?mode=ro"
    with closing(sqlite3.connect(source_uri, uri=True)) as source:
        users = source.execute(
            "SELECT full_name, email, password_hash, is_active, created_at FROM users"
        ).fetchall()

    auth_store.initialize_auth_database()
    imported = 0
    skipped = 0
    connection = psycopg.connect(database_url, connect_timeout=5, sslmode="require")
    try:
        with connection:
            for user in users:
                result = connection.execute(
                    "INSERT INTO users "
                    "(full_name, email, password_hash, is_active, created_at) "
                    "VALUES (%s, %s, %s, %s, %s) "
                    "ON CONFLICT(email) DO NOTHING RETURNING id",
                    user,
                ).fetchone()
                if result:
                    imported += 1
                else:
                    skipped += 1
    finally:
        connection.close()
    return imported, skipped


if __name__ == "__main__":
    imported_count, skipped_count = migrate_users()
    print(
        f"Imported {imported_count} user account(s); "
        f"skipped {skipped_count} already-existing account(s)."
    )