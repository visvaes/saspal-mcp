import hashlib
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


AUTH_DATABASE_PATH = Path(__file__).resolve().with_name("auth.sqlite3")
SESSION_TTL_SECONDS = 8 * 60 * 60
PASSWORD_RESET_TTL_SECONDS = 30 * 60


@contextmanager
def _connection() -> Iterator[sqlite3.Connection]:
    AUTH_DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(AUTH_DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def initialize_auth_database() -> None:
    with _connection() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                full_name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS auth_sessions (
                token_hash TEXT PRIMARY KEY,
                csrf_hash TEXT NOT NULL,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS password_reset_tokens (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at REAL NOT NULL,
                expires_at REAL NOT NULL,
                used_at REAL
            );

            CREATE INDEX IF NOT EXISTS ix_auth_sessions_user_id
                ON auth_sessions(user_id);
            CREATE INDEX IF NOT EXISTS ix_auth_sessions_expires_at
                ON auth_sessions(expires_at);
            CREATE INDEX IF NOT EXISTS ix_password_reset_user_id
                ON password_reset_tokens(user_id);
            """
        )
        now = time.time()
        connection.execute("DELETE FROM auth_sessions WHERE expires_at <= ?", (now,))
        connection.execute(
            "DELETE FROM password_reset_tokens WHERE expires_at <= ? OR used_at IS NOT NULL",
            (now,),
        )


def create_user(full_name: str, email: str, password_hash: str) -> dict[str, object]:
    with _connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO users (full_name, email, password_hash, created_at)
            VALUES (?, ?, ?, ?)
            """,
            (full_name.strip(), normalize_email(email), password_hash, time.time()),
        )
        return {
            "id": cursor.lastrowid,
            "full_name": full_name.strip(),
            "email": normalize_email(email),
        }


def get_user_by_email(email: str) -> dict[str, object] | None:
    with _connection() as connection:
        row = connection.execute(
            "SELECT id, full_name, email, password_hash, is_active FROM users WHERE email = ?",
            (normalize_email(email),),
        ).fetchone()
    return dict(row) if row else None


def create_session(user_id: int) -> tuple[str, str]:
    session_token = secrets.token_urlsafe(32)
    csrf_token = secrets.token_urlsafe(32)
    now = time.time()
    with _connection() as connection:
        connection.execute(
            "INSERT INTO auth_sessions (token_hash, csrf_hash, user_id, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                _digest(session_token),
                _digest(csrf_token),
                user_id,
                now,
                now + SESSION_TTL_SECONDS,
            ),
        )
    return session_token, csrf_token


def get_session(session_token: str) -> dict[str, object] | None:
    with _connection() as connection:
        row = connection.execute(
            """
            SELECT sessions.csrf_hash, users.id, users.full_name, users.email, users.is_active
            FROM auth_sessions AS sessions
            JOIN users ON users.id = sessions.user_id
            WHERE sessions.token_hash = ? AND sessions.expires_at > ?
            """,
            (_digest(session_token), time.time()),
        ).fetchone()
    if not row or not row["is_active"]:
        return None
    return dict(row)


def revoke_session(session_token: str) -> None:
    with _connection() as connection:
        connection.execute(
            "DELETE FROM auth_sessions WHERE token_hash = ?", (_digest(session_token),)
        )


def create_password_reset_token(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    now = time.time()
    with _connection() as connection:
        connection.execute(
            "DELETE FROM password_reset_tokens WHERE user_id = ?", (user_id,)
        )
        connection.execute(
            """
            INSERT INTO password_reset_tokens (token_hash, user_id, created_at, expires_at)
            VALUES (?, ?, ?, ?)
            """,
            (_digest(token), user_id, now, now + PASSWORD_RESET_TTL_SECONDS),
        )
    return token


def delete_password_reset_token(token: str) -> None:
    with _connection() as connection:
        connection.execute(
            "DELETE FROM password_reset_tokens WHERE token_hash = ?", (_digest(token),)
        )


def reset_password(token: str, password_hash: str) -> bool:
    now = time.time()
    with _connection() as connection:
        row = connection.execute(
            """
            SELECT user_id FROM password_reset_tokens
            WHERE token_hash = ? AND expires_at > ? AND used_at IS NULL
            """,
            (_digest(token), now),
        ).fetchone()
        if not row:
            return False

        user_id = row["user_id"]
        connection.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, user_id),
        )
        connection.execute(
            "UPDATE password_reset_tokens SET used_at = ? WHERE token_hash = ?",
            (now, _digest(token)),
        )
        connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
    return True
