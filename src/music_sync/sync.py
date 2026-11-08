from __future__ import annotations

import json
import threading
from collections.abc import Callable

from .database import SyncDatabase
from .matching import choose
from .models import Candidate, SyncPair, SyncResult, Track
from .notify import DiscordNotifier
from .providers import AppleProvider, NeteaseProvider, storefront_for_locale


class SyncService:
    def __init__(self, db: SyncDatabase, apple: AppleProvider, netease: NeteaseProvider, notifier: DiscordNotifier | None = None) -> None:
        self.db, self.apple, self.netease = db, apple, netease
        self.notifier = notifier or DiscordNotifier()
        self._locks: dict[str, threading.Lock] = {}

    def sync(self, pair: SyncPair) -> SyncResult:
        lock = self._locks.setdefault(pair.id, threading.Lock())
        if not lock.acquire(blocking=False):
            return SyncResult(-1, "already_running", error="sync already running")
        run_id = self.db.start_run(pair.id)
        result = SyncResult(run_id, "running")
        try:
            source_tracks = self.apple.tracks(pair.apple_playlist)
            source_ids = {track.id for track in source_tracks}
            destination = {track.id: track for track in self.netease.playlist_tracks(pair.netease_playlist)}
            state = self.db.state(pair.id)
            initial_done = bool(state and state["initial_done"])
            unresolved = False
            for index, source in enumerate(source_tracks):
                self.db.update_run(run_id, current_source_id=source.id)
                mapping = self.db.mapping(pair.id, source.id)
                if mapping and mapping["state"] == "synced" and mapping["manual"]:
                    self._ensure_manual(pair, source, mapping["dest_id"], destination, result)
                    result.skipped += 1
                    continue
                if mapping and mapping["state"] == "synced" and mapping["dest_id"] in destination:
                    result.skipped += 1
                    continue
                try:
                    storefront = storefront_for_locale(source.audio_locale)
                    try:
                        canonical = self.apple.metadata(source, storefront)
                    except Exception:
                        canonical = self.apple.metadata(source, "cn")
                        canonical.raw["fallback_storefront"] = True
                    self.db.set_source(pair.id, canonical)
                    candidates = self.netease.search(f"{canonical.title} {canonical.artist}")
                    match, ranked = choose(canonical, candidates)
                    if not match:
                        unresolved = True
                        result.ambiguous += 1
                        self.db.save_mapping(pair.id, source.id, None, "ambiguous", error="no confident unique match", candidates=[self._candidate_json(item) for item in ranked[:10]])
                        self.notifier.send("Netease match failed", {"pair": pair.name, "title": canonical.title, "artist": canonical.artist, "storefront": canonical.storefront or storefront})
                        continue
                    if match.id not in destination:
                        self.netease.add(pair.netease_playlist, match.id)
                        destination[match.id] = match
                        result.added += 1
                        self.notifier.send("Song added to Netease", {"pair": pair.name, "source": f"{canonical.title} — {canonical.artist}", "storefront": canonical.storefront or storefront, "netease": match.url or match.id})
                    self.db.save_mapping(pair.id, source.id, match.id, "synced")
                except Exception as exc:
                    unresolved = True
                    result.failed += 1
                    self.db.save_mapping(pair.id, source.id, None, "failed", error=str(exc))
                    self.notifier.send("Song sync failed", {"pair": pair.name, "source": f"{source.title} — {source.artist}", "error": str(exc)})
            if pair.removal_policy == "mirror" and (initial_done or pair.initial_policy == "mirror") and not unresolved:
                mapped = self.db.mappings(pair.id)
                if not initial_done and pair.initial_policy == "mirror":
                    keep = {row["dest_id"] for row in mapped if row["source_id"] in source_ids and row["state"] == "synced" and row["dest_id"]}
                    for destination_id in list(destination):
                        if destination_id not in keep:
                            self.netease.remove(pair.netease_playlist, destination_id)
                            result.removed += 1
                for mapping in mapped:
                    if mapping["source_id"] not in source_ids and mapping["dest_id"]:
                        other = [row for row in mapped if row["dest_id"] == mapping["dest_id"] and row["source_id"] in source_ids]
                        if not other and mapping["dest_id"] in destination:
                            self.netease.remove(pair.netease_playlist, mapping["dest_id"])
                            result.removed += 1
            if not unresolved and not initial_done:
                self.db.set_initial_done(pair.id, True)
            self.db.set_last_success(pair.id)
            result.status = "partial" if unresolved else "success"
            self.db.finish_run(run_id, result.status, added=result.added, skipped=result.skipped, removed=result.removed, failed=result.failed, ambiguous=result.ambiguous)
            return result
        except Exception as exc:
            result.status, result.error = "failed", str(exc)
            self.db.finish_run(run_id, "failed", error=str(exc), added=result.added, skipped=result.skipped, removed=result.removed, failed=result.failed, ambiguous=result.ambiguous)
            return result
        finally:
            lock.release()

    def correct(self, pair: SyncPair, source_id: str, netease_id: str) -> None:
        mapping = self.db.mapping(pair.id, source_id)
        if mapping and mapping["dest_id"] and mapping["dest_id"] != netease_id:
            self.netease.remove(pair.netease_playlist, mapping["dest_id"])
        current = {track.id for track in self.netease.playlist_tracks(pair.netease_playlist)}
        if netease_id not in current:
            self.netease.add(pair.netease_playlist, netease_id)
        self.db.save_mapping(pair.id, source_id, netease_id, "synced", manual=True)
        self.notifier.send("Manual Netease correction applied", {"pair": pair.name, "source_id": source_id, "netease": f"https://music.163.com/#/song?id={netease_id}"})

    def _ensure_manual(self, pair: SyncPair, source: Track, netease_id: str, destination: dict[str, Candidate], result: SyncResult) -> None:
        if netease_id not in destination:
            self.netease.add(pair.netease_playlist, netease_id)
            destination[netease_id] = self.netease.song(netease_id)
            result.added += 1

    @staticmethod
    def _candidate_json(candidate: Candidate) -> dict[str, object]:
        return {"id": candidate.id, "title": candidate.title, "artist": candidate.artist, "album": candidate.album, "score": candidate.score}
