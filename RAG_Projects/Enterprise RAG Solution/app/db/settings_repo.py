"""Persisted runtime settings (JSON values, never secrets)."""

from __future__ import annotations

import json
import logging
from typing import Any

from app.db.models import AppSetting
from app.db.session import Database

logger = logging.getLogger("app.db")


class SettingsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    def get(self, key: str) -> dict[str, Any] | None:
        with self._db.session() as session:
            row = session.get(AppSetting, key)
            if row is None:
                return None
            try:
                value = json.loads(row.value_json)
            except ValueError:
                logger.warning("settings_corrupt key=%s", key)
                return None
            return value if isinstance(value, dict) else None

    def set(self, key: str, value: dict[str, Any]) -> None:
        payload = json.dumps(value, ensure_ascii=False)
        with self._db.session() as session:
            row = session.get(AppSetting, key)
            if row is None:
                session.add(AppSetting(key=key, value_json=payload))
            else:
                row.value_json = payload

    def delete(self, key: str) -> bool:
        with self._db.session() as session:
            row = session.get(AppSetting, key)
            if row is None:
                return False
            session.delete(row)
            return True
