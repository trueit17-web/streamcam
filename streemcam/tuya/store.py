import json
import sqlite3
import time
from pathlib import Path

from .models import TuyaCredentials

SCHEMA = """
CREATE TABLE IF NOT EXISTS tuya_account (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    user_code TEXT NOT NULL,
    terminal_id TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    token_json TEXT NOT NULL,
    updated_at REAL NOT NULL
);
"""


class TuyaStore:
    def __init__(self, path: str):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._db.executescript(SCHEMA)

    def save(self, creds: TuyaCredentials) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO tuya_account "
            "(id, user_code, terminal_id, endpoint, token_json, updated_at) VALUES (1, ?, ?, ?, ?, ?)",
            (creds.user_code, creds.terminal_id, creds.endpoint,
             json.dumps(creds.token_info), time.time()))

    def load(self) -> TuyaCredentials | None:
        row = self._db.execute(
            "SELECT user_code, terminal_id, endpoint, token_json FROM tuya_account WHERE id = 1").fetchone()
        if row is None:
            return None
        user_code, terminal_id, endpoint, token_json = row
        return TuyaCredentials(user_code, terminal_id, endpoint, json.loads(token_json))

    def update_token(self, token_info: dict) -> None:
        self._db.execute("UPDATE tuya_account SET token_json = ?, updated_at = ? WHERE id = 1",
                         (json.dumps(token_info), time.time()))

    def clear(self) -> None:
        self._db.execute("DELETE FROM tuya_account")
