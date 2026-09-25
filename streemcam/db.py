import sqlite3
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
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.executescript(SCHEMA)

    def add_allowed(self, ident: Identity, added_by: int | None) -> bool:
        cur = self._db.execute(
            "INSERT OR IGNORE INTO allowed (platform, user_id, added_by, added_at) VALUES (?, ?, ?, ?)",
            (ident.platform, ident.user_id, added_by, time.time()))
        return cur.rowcount == 1

    def remove_allowed(self, ident: Identity) -> bool:
        cur = self._db.execute("DELETE FROM allowed WHERE platform = ? AND user_id = ?",
                               (ident.platform, ident.user_id))
        return cur.rowcount == 1

    def is_allowed(self, ident: Identity) -> bool:
        row = self._db.execute("SELECT 1 FROM allowed WHERE platform = ? AND user_id = ?",
                               (ident.platform, ident.user_id)).fetchone()
        return row is not None

    def list_allowed(self) -> list[Identity]:
        rows = self._db.execute("SELECT platform, user_id FROM allowed ORDER BY platform, user_id")
        return [Identity(p, u) for p, u in rows]

    def add_link_token(self, jti: str, ident: Identity, expires_at: float) -> None:
        self._db.execute("DELETE FROM link_tokens WHERE expires_at < ?", (time.time() - 86400,))
        self._db.execute(
            "INSERT INTO link_tokens (jti, platform, user_id, expires_at) VALUES (?, ?, ?, ?)",
            (jti, ident.platform, ident.user_id, expires_at))

    def use_link_token(self, jti: str, now: float) -> bool:
        cur = self._db.execute(
            "UPDATE link_tokens SET used = 1 WHERE jti = ? AND used = 0 AND expires_at >= ?",
            (jti, now))
        return cur.rowcount == 1
