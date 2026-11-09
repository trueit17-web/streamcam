import sqlite3
import threading
import time
from pathlib import Path

from .identity import Identity

SCHEMA = """
CREATE TABLE IF NOT EXISTS allowed (
    platform TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    added_by INTEGER,
    added_at REAL NOT NULL,
    PRIMARY KEY (platform, user_id)
);
CREATE TABLE IF NOT EXISTS link_tokens (
    jti TEXT PRIMARY KEY,
    platform TEXT NOT NULL,
    user_id INTEGER NOT NULL,
    expires_at REAL NOT NULL,
    used INTEGER NOT NULL DEFAULT 0
);
"""


class Store:
    """Одно соединение SQLite на весь процесс.

    Запросы приходят из пула потоков FastAPI одновременно (сайт грузит много
    снимков сразу), а одно соединение sqlite3 нельзя использовать из нескольких
    потоков одновременно: получаем InterfaceError. Поэтому всё под замком.
    """

    def __init__(self, path: str):
        self._lock = threading.Lock()
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.executescript(SCHEMA)

    def _run(self, *statements: tuple) -> tuple[list, int]:
        """Выполняет запросы под замком; возвращает строки и rowcount последнего."""
        with self._lock:
            for sql, params in statements:
                cur = self._db.execute(sql, params)
            return cur.fetchall(), cur.rowcount

    def add_allowed(self, ident: Identity, added_by: int | None) -> bool:
        _, n = self._run((
            "INSERT OR IGNORE INTO allowed (platform, user_id, added_by, added_at) VALUES (?, ?, ?, ?)",
            (ident.platform, ident.user_id, added_by, time.time())))
        return n == 1

    def remove_allowed(self, ident: Identity) -> bool:
        _, n = self._run(("DELETE FROM allowed WHERE platform = ? AND user_id = ?",
                          (ident.platform, ident.user_id)))
        return n == 1

    def is_allowed(self, ident: Identity) -> bool:
        rows, _ = self._run(("SELECT 1 FROM allowed WHERE platform = ? AND user_id = ?",
                             (ident.platform, ident.user_id)))
        return bool(rows)

    def list_allowed(self) -> list[Identity]:
        rows, _ = self._run(("SELECT platform, user_id FROM allowed ORDER BY platform, user_id", ()))
        return [Identity(p, u) for p, u in rows]

    def add_link_token(self, jti: str, ident: Identity, expires_at: float) -> None:
        self._run(
            ("DELETE FROM link_tokens WHERE expires_at < ?", (time.time() - 86400,)),
            ("INSERT INTO link_tokens (jti, platform, user_id, expires_at) VALUES (?, ?, ?, ?)",
             (jti, ident.platform, ident.user_id, expires_at)))

    def use_link_token(self, jti: str, now: float) -> bool:
        _, n = self._run((
            "UPDATE link_tokens SET used = 1 WHERE jti = ? AND used = 0 AND expires_at >= ?",
            (jti, now)))
        return n == 1
