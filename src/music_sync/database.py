from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import Track


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SyncDatabase:
    def __init__(self, path: str | Path = "data/sync.sqlite3") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        self.conn.execute("PRAGMA foreign_keys = ON")
        self._migrate()

    def _migrate(self) -> None:
        with self.lock, self.conn:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL);
                INSERT INTO schema_version(version) SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);
                CREATE TABLE IF NOT EXISTS sync_pairs_state (
                  pair_id TEXT PRIMARY KEY, initial_done INTEGER NOT NULL DEFAULT 0,
                  last_success TEXT, last_error TEXT
                );
                CREATE TABLE IF NOT EXISTS source_tracks (
                  pair_id TEXT NOT NULL, source_id TEXT NOT NULL, catalog_id TEXT,
                  isrc TEXT, title TEXT NOT NULL, artist TEXT NOT NULL, album TEXT,
                  duration_ms INTEGER, audio_locale TEXT, storefront TEXT, raw_json TEXT,
                  fetched_at TEXT NOT NULL, PRIMARY KEY(pair_id, source_id)
                );
                CREATE TABLE IF NOT EXISTS song_mappings (
                  pair_id TEXT NOT NULL, source_id TEXT NOT NULL, dest_id TEXT,
                  state TEXT NOT NULL, manual INTEGER NOT NULL DEFAULT 0,
                  last_error TEXT, candidates_json TEXT, created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL, PRIMARY KEY(pair_id, source_id)
                );
                CREATE TABLE IF NOT EXISTS sync_runs (
                  id INTEGER PRIMARY KEY AUTOINCREMENT, pair_id TEXT NOT NULL,
                  started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
                  current_source_id TEXT, added INTEGER NOT NULL DEFAULT 0,
                  skipped INTEGER NOT NULL DEFAULT 0, removed INTEGER NOT NULL DEFAULT 0,
                  failed INTEGER NOT NULL DEFAULT 0, ambiguous INTEGER NOT NULL DEFAULT 0,
                  error TEXT
                );
                """
            )
            columns = {row[1] for row in self.conn.execute("PRAGMA table_info(sync_runs)")}
            for name, declaration in {"total": "INTEGER", "processed": "INTEGER NOT NULL DEFAULT 0", "current_source_title": "TEXT", "cancel_requested": "INTEGER NOT NULL DEFAULT 0"}.items():
                if name not in columns:
                    self.conn.execute(f"ALTER TABLE sync_runs ADD COLUMN {name} {declaration}")
            mapping_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(song_mappings)")}
            for name in ("dest_json", "match_method"):
                if name not in mapping_columns:
                    self.conn.execute(f"ALTER TABLE song_mappings ADD COLUMN {name} TEXT")
            self.conn.execute("CREATE TABLE IF NOT EXISTS run_changes (id INTEGER PRIMARY KEY, run_id INTEGER NOT NULL, action TEXT NOT NULL, target_id TEXT NOT NULL, previous_json TEXT, undone INTEGER NOT NULL DEFAULT 0, UNIQUE(run_id, action, target_id))")
            self.conn.execute("UPDATE schema_version SET version = 3")

    def start_run(self, pair_id: str) -> int:
        with self.lock, self.conn:
            cur = self.conn.execute("INSERT INTO sync_runs(pair_id, started_at, status) VALUES (?, ?, 'running')", (pair_id, now_iso()))
            return int(cur.lastrowid)

    def update_run(self, run_id: int, **values: Any) -> None:
        if not values:
            return
        assignments = ", ".join(f"{key} = ?" for key in values)
        with self.lock, self.conn:
            self.conn.execute(f"UPDATE sync_runs SET {assignments} WHERE id = ?", [*values.values(), run_id])

    def finish_run(self, run_id: int, status: str, **values: Any) -> None:
        values.update(finished_at=now_iso(), status=status)
        self.update_run(run_id, **values)

    def set_source(self, pair_id: str, track: Track) -> None:
        with self.lock, self.conn:
            self.conn.execute(
                """INSERT INTO source_tracks(pair_id, source_id, catalog_id, isrc, title, artist, album,
                   duration_ms, audio_locale, storefront, raw_json, fetched_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(pair_id, source_id) DO UPDATE SET catalog_id=excluded.catalog_id,
                   isrc=excluded.isrc, title=excluded.title, artist=excluded.artist, album=excluded.album,
                   duration_ms=excluded.duration_ms, audio_locale=excluded.audio_locale,
                   storefront=excluded.storefront, raw_json=excluded.raw_json, fetched_at=excluded.fetched_at""",
                (pair_id, track.id, track.raw.get("catalog_id"), track.isrc, track.title, track.artist,
                 track.album, track.duration_ms, track.audio_locale, track.storefront,
                 json.dumps(track.raw), now_iso()),
            )

    def mappings(self, pair_id: str) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute("SELECT * FROM song_mappings WHERE pair_id = ?", (pair_id,)).fetchall()

    def mapping(self, pair_id: str, source_id: str) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute("SELECT * FROM song_mappings WHERE pair_id = ? AND source_id = ?", (pair_id, source_id)).fetchone()

    def save_mapping(self, pair_id: str, source_id: str, dest_id: str | None, state: str, manual: bool = False, error: str | None = None, candidates: list[dict[str, Any]] | None = None, destination: dict | None = None, match_method: str | None = None) -> None:
        stamp = now_iso()
        with self.lock, self.conn:
            self.conn.execute(
                """INSERT INTO song_mappings(pair_id, source_id, dest_id, state, manual, last_error,
                   candidates_json, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(pair_id, source_id) DO UPDATE SET dest_id=excluded.dest_id,
                   state=excluded.state, manual=excluded.manual, last_error=excluded.last_error,
                   candidates_json=excluded.candidates_json, updated_at=excluded.updated_at""",
                (pair_id, source_id, dest_id, state, int(manual), error, json.dumps(candidates or []), stamp, stamp),
            )
            self.conn.execute("UPDATE song_mappings SET dest_json = ?, match_method = ? WHERE pair_id = ? AND source_id = ?", (json.dumps(destination) if destination else None, match_method, pair_id, source_id))

    def mapping_details(self, pair_id: str) -> list[dict]:
        with self.lock:
            rows = self.conn.execute("SELECT m.*, s.title, s.artist, s.album, s.audio_locale, s.storefront, s.raw_json FROM song_mappings m LEFT JOIN source_tracks s ON m.pair_id = s.pair_id AND m.source_id = s.source_id WHERE m.pair_id = ? ORDER BY m.updated_at DESC", (pair_id,)).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["destination"] = json.loads(item.get("dest_json") or "{}")
            item["candidates"] = json.loads(item.get("candidates_json") or "[]")
            result.append(item)
        return result

    def record_change(self, run_id: int, action: str, target_id: str, previous: dict | None = None) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT OR IGNORE INTO run_changes(run_id, action, target_id, previous_json) VALUES (?, ?, ?, ?)", (run_id, action, target_id, json.dumps(previous) if previous is not None else None))

    def changes(self, run_id: int) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute("SELECT * FROM run_changes WHERE run_id = ? AND undone = 0 ORDER BY id DESC", (run_id,)).fetchall()

    def mark_undone(self, change_id: int) -> None:
        with self.lock, self.conn:
            self.conn.execute("UPDATE run_changes SET undone = 1 WHERE id = ?", (change_id,))

    def restore_mapping(self, pair_id: str, source_id: str, previous: dict | None) -> None:
        with self.lock, self.conn:
            self.conn.execute("DELETE FROM song_mappings WHERE pair_id = ? AND source_id = ?", (pair_id, source_id))
            if previous:
                columns = list(previous)
                self.conn.execute(f"INSERT INTO song_mappings ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})", list(previous.values()))

    def request_stop(self, run_id: int) -> bool:
        with self.lock, self.conn:
            cursor = self.conn.execute("UPDATE sync_runs SET cancel_requested = 1, status = 'stopping' WHERE id = ? AND status IN ('running', 'stopping')", (run_id,))
            return cursor.rowcount > 0

    def set_initial_done(self, pair_id: str, done: bool, error: str | None = None) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO sync_pairs_state(pair_id, initial_done, last_error) VALUES (?, ?, ?) ON CONFLICT(pair_id) DO UPDATE SET initial_done=excluded.initial_done, last_error=excluded.last_error", (pair_id, int(done), error))

    def state(self, pair_id: str) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute("SELECT * FROM sync_pairs_state WHERE pair_id = ?", (pair_id,)).fetchone()

    def set_last_success(self, pair_id: str) -> None:
        with self.lock, self.conn:
            self.conn.execute("INSERT INTO sync_pairs_state(pair_id, last_success) VALUES (?, ?) ON CONFLICT(pair_id) DO UPDATE SET last_success=excluded.last_success, last_error=NULL", (pair_id, now_iso()))

    def recent_runs(self, limit: int = 20) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute("SELECT * FROM sync_runs ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def run(self, run_id: int) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute("SELECT * FROM sync_runs WHERE id = ?", (run_id,)).fetchone()

    def interrupt_runs(self) -> None:
        with self.lock, self.conn:
            self.conn.execute("UPDATE sync_runs SET status = 'interrupted', finished_at = ?, error = 'Service restarted before run completed; inspect run changes before retrying' WHERE status IN ('running', 'stopping', 'reverting')", (now_iso(),))

    def latest_run(self, pair_id: str) -> sqlite3.Row | None:
        with self.lock:
            return self.conn.execute("SELECT * FROM sync_runs WHERE pair_id = ? ORDER BY id DESC LIMIT 1", (pair_id,)).fetchone()
