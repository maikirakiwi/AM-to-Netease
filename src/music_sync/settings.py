from __future__ import annotations

import json
import os
import secrets
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .models import SyncPair


DEFAULT_SETTINGS: dict[str, Any] = {
    "version": 1,
    "server": {"host": "0.0.0.0", "port": 8008},
    "schedule": {"enabled": True, "time": "03:00", "timezone": "Asia/Shanghai", "catch_up": True},
    "apple": {"developer_token": "", "music_user_token": ""},
    "netease": {"session": {}},
    "discord": {"webhook_url": ""},
    "sync_pairs": [],
}


class SettingsError(ValueError):
    pass


class SettingsStore:
    def __init__(self, path: str | Path = "settings.json") -> None:
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return json.loads(json.dumps(DEFAULT_SETTINGS))
        mode = self.path.stat().st_mode & 0o777
        if mode & 0o077:
            raise SettingsError(f"{self.path} must only be readable by its owner (chmod 600)")
        with self.path.open(encoding="utf-8") as handle:
            data = json.load(handle)
        self.validate(data)
        return data

    def save(self, data: dict[str, Any]) -> None:
        self.validate(data)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self.path)
            os.chmod(self.path, 0o600)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)

    @staticmethod
    def validate(data: dict[str, Any]) -> None:
        for key in ("server", "schedule", "apple", "netease", "discord", "sync_pairs"):
            if key not in data:
                raise SettingsError(f"missing settings key: {key}")
        time = data["schedule"].get("time", "03:00")
        try:
            hour, minute = (int(value) for value in time.split(":", 1))
        except Exception as exc:
            raise SettingsError("schedule.time must be HH:MM") from exc
        if not 0 <= hour <= 23 or not 0 <= minute <= 59:
            raise SettingsError("schedule.time must be HH:MM")
        for pair in data["sync_pairs"]:
            if pair.get("initial_policy", "preserve") not in {"preserve", "mirror"}:
                raise SettingsError("initial_policy must be preserve or mirror")
            if pair.get("removal_policy", "mirror") not in {"preserve", "mirror"}:
                raise SettingsError("removal_policy must be preserve or mirror")

    def pairs(self) -> list[SyncPair]:
        data = self.load()
        changed = False
        pairs = []
        for pair in data["sync_pairs"]:
            if not pair.get("id"):
                pair["id"] = secrets.token_hex(8)
                changed = True
            pairs.append(SyncPair(**pair))
        if changed:
            self.save(data)
        return pairs

    def upsert_pair(self, pair: SyncPair) -> None:
        data = self.load()
        pairs = data["sync_pairs"]
        record = asdict(pair)
        for index, current in enumerate(pairs):
            if current.get("id") == pair.id:
                pairs[index] = record
                break
        else:
            pairs.append(record)
        self.save(data)

    def delete_pair(self, pair_id: str) -> None:
        data = self.load()
        data["sync_pairs"] = [pair for pair in data["sync_pairs"] if pair.get("id") != pair_id]
        self.save(data)
