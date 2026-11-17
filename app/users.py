"""Cuentas de usuario y claves de API persistidas en SQLite (solo biblioteca estándar).

* Las contraseñas se guardan con **scrypt** y sal aleatoria por usuario.
* De las claves de API solo se guarda su hash SHA-256 y un prefijo para
  identificarlas: el valor completo se muestra una única vez, al crearlas.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import sqlite3
import threading
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
API_KEY_PREFIX = "gpx_"
MAX_KEYS_PER_USER = 20

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS api_keys (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    prefix       TEXT NOT NULL,
    key_hash     TEXT NOT NULL UNIQUE,
    created_at   TEXT NOT NULL,
    last_used_at TEXT
);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


class UsernameTakenError(Exception):
    """Ya existe una cuenta con ese nombre."""


class TooManyKeysError(Exception):
    """El usuario ha alcanzado el máximo de claves de API."""


@dataclass(frozen=True)
class User:
    id: int
    username: str
    password_hash: str
    is_admin: bool
    created_at: datetime


@dataclass(frozen=True)
class ApiKey:
    id: int
    name: str
    prefix: str
    created_at: datetime
    last_used_at: datetime | None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_password(password: str) -> str:
    """Deriva el hash scrypt de una contraseña (formato ``scrypt$n$r$p$sal$hash``). Es costoso a propósito."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32)
    enc = base64.b64encode
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${enc(salt).decode()}${enc(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    """Comprueba una contraseña contra su hash, en tiempo constante."""
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        expected = base64.b64decode(digest)
        actual = hashlib.scrypt(
            password.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=len(expected)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def hash_api_key(key: str) -> str:
    """Hash de una clave de API (aleatoria y larga: basta SHA-256, sin sal)."""
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key() -> str:
    """Nueva clave de API con 256 bits de entropía."""
    return API_KEY_PREFIX + secrets.token_urlsafe(32)


class UserStore:
    """Acceso a la base de datos de usuarios. Seguro entre hilos (una conexión por operación)."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = threading.Lock()
        path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn, conn:
            conn.executescript(_SCHEMA)
        try:
            path.chmod(0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _user(row: sqlite3.Row | None) -> User | None:
        if row is None:
            return None
        return User(
            id=row["id"],
            username=row["username"],
            password_hash=row["password_hash"],
            is_admin=bool(row["is_admin"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    # --- Usuarios --------------------------------------------------------
    def create_user(self, username: str, password_hash: str, is_admin: bool | None = None) -> User:
        """Crea una cuenta. Si ``is_admin`` es ``None``, la primera cuenta del sistema es administradora.

        Raises:
            UsernameTakenError: el nombre ya existe (sin distinguir mayúsculas).
        """
        with self._lock, closing(self._connect()) as conn, conn:
            first = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0 if is_admin is None else is_admin
            try:
                cur = conn.execute(
                    "INSERT INTO users (username, password_hash, is_admin, created_at) VALUES (?, ?, ?, ?)",
                    (username, password_hash, int(first), _now()),
                )
            except sqlite3.IntegrityError as exc:
                raise UsernameTakenError(username) from exc
            row = conn.execute("SELECT * FROM users WHERE id = ?", (cur.lastrowid,)).fetchone()
        user = self._user(row)
        assert user is not None
        return user

    def get_user(self, username: str) -> User | None:
        with closing(self._connect()) as conn:
            return self._user(conn.execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone())

    def get_user_by_id(self, user_id: int) -> User | None:
        with closing(self._connect()) as conn:
            return self._user(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())

    def list_users(self) -> list[tuple[User, int]]:
        """Todas las cuentas con su número de claves de API, por orden de alta."""
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT u.*, (SELECT COUNT(*) FROM api_keys k WHERE k.user_id = u.id) AS key_count "
                "FROM users u ORDER BY u.id"
            ).fetchall()
        return [(user, row["key_count"]) for row in rows if (user := self._user(row)) is not None]

    def set_admin(self, user_id: int, is_admin: bool) -> None:
        with self._lock, closing(self._connect()) as conn, conn:
            conn.execute("UPDATE users SET is_admin = ? WHERE id = ?", (int(is_admin), user_id))

    def delete_user(self, user_id: int) -> bool:
        """Elimina la cuenta y, en cascada, sus claves de API."""
        with self._lock, closing(self._connect()) as conn, conn:
            return conn.execute("DELETE FROM users WHERE id = ?", (user_id,)).rowcount > 0

    def set_password(self, user_id: int, password_hash: str) -> None:
        with self._lock, closing(self._connect()) as conn, conn:
            conn.execute("UPDATE users SET password_hash = ? WHERE id = ?", (password_hash, user_id))

    # --- Claves de API ---------------------------------------------------
    def create_api_key(self, user_id: int, name: str) -> tuple[ApiKey, str]:
        """Crea una clave y devuelve ``(metadatos, clave en claro)``. La clave no se puede recuperar después.

        Raises:
            TooManyKeysError: se superó :data:`MAX_KEYS_PER_USER`.
        """
        key = generate_api_key()
        prefix = key[: len(API_KEY_PREFIX) + 6]
        with self._lock, closing(self._connect()) as conn, conn:
            count = conn.execute("SELECT COUNT(*) FROM api_keys WHERE user_id = ?", (user_id,)).fetchone()[0]
            if count >= MAX_KEYS_PER_USER:
                raise TooManyKeysError
            now = _now()
            cur = conn.execute(
                "INSERT INTO api_keys (user_id, name, prefix, key_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                (user_id, name, prefix, hash_api_key(key), now),
            )
        assert cur.lastrowid is not None
        return ApiKey(cur.lastrowid, name, prefix, datetime.fromisoformat(now), None), key

    def list_api_keys(self, user_id: int) -> list[ApiKey]:
        with closing(self._connect()) as conn:
            rows = conn.execute(
                "SELECT * FROM api_keys WHERE user_id = ? ORDER BY created_at DESC, id DESC", (user_id,)
            ).fetchall()
        return [
            ApiKey(
                id=r["id"],
                name=r["name"],
                prefix=r["prefix"],
                created_at=datetime.fromisoformat(r["created_at"]),
                last_used_at=datetime.fromisoformat(r["last_used_at"]) if r["last_used_at"] else None,
            )
            for r in rows
        ]

    def delete_api_key(self, user_id: int, key_id: int) -> bool:
        """Revoca una clave del usuario. Devuelve ``False`` si no existía o no era suya."""
        with self._lock, closing(self._connect()) as conn, conn:
            cur = conn.execute("DELETE FROM api_keys WHERE id = ? AND user_id = ?", (key_id, user_id))
            return cur.rowcount > 0

    def user_for_api_key(self, key: str) -> User | None:
        """Usuario propietario de una clave válida (y anota su último uso)."""
        with self._lock, closing(self._connect()) as conn, conn:
            row = conn.execute(
                "SELECT u.*, k.id AS key_id FROM api_keys k JOIN users u ON u.id = k.user_id WHERE k.key_hash = ?",
                (hash_api_key(key),),
            ).fetchone()
            if row is None:
                return None
            conn.execute("UPDATE api_keys SET last_used_at = ? WHERE id = ?", (_now(), row["key_id"]))
        return self._user(row)

    # --- Secreto de sesión -----------------------------------------------
    def session_secret(self) -> str:
        """Secreto aleatorio persistente para firmar sesiones (se crea la primera vez)."""
        with self._lock, closing(self._connect()) as conn, conn:
            row = conn.execute("SELECT value FROM meta WHERE key = 'session_secret'").fetchone()
            if row is not None:
                return str(row["value"])
            secret = secrets.token_hex(32)
            conn.execute("INSERT INTO meta (key, value) VALUES ('session_secret', ?)", (secret,))
            return secret
