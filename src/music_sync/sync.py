from __future__ import annotations

import logging

import json
import threading

from .database import SyncDatabase
from .matching import choose, contains_lyric_line
from .models import Candidate, SyncPair, SyncResult, Track
from .notify import DiscordNotifier
from .providers import AppleProvider, NeteaseProvider, storefront_for_locale


logger = logging.getLogger(__name__)

class SyncCancelled(Exception):
    pass


class SyncService:
    def __init__(self, db: SyncDatabase, apple: AppleProvider, netease: NeteaseProvider, notifier: DiscordNotifier | None = None) -> None:
        self.db, self.apple, self.netease = db, apple, netease
        self.notifier = notifier or DiscordNotifier()
        self._locks: dict[str, threading.Lock] = {}

    def sync(self, pair: SyncPair, *, background: bool = False) -> SyncResult:
        lock = self._locks.setdefault(pair.id, threading.Lock())
        if not lock.acquire(blocking=False):
            return SyncResult(-1, "already_running", error="sync already running")
        previous = self.db.latest_run(pair.id)
        if previous and previous["status"] in {"rollback_failed", "interrupted"} and self.db.changes(previous["id"]):
            lock.release()
            return SyncResult(previous["id"], "revert_required", error="Complete the previous run's revert before syncing again")
        try:
            run_id = self.db.start_run(pair.id)
        except Exception:
            lock.release()
            raise
        if background:
            try:
                threading.Thread(target=self._run, args=(pair, lock, run_id), daemon=True).start()
            except Exception:
                lock.release()
                self.db.finish_run(run_id, "failed", error="could not start background worker")
                raise
            return SyncResult(run_id, "running")
        return self._run(pair, lock, run_id)

    def _check_cancel(self, run_id: int) -> None:
        if self.db.run(run_id)["cancel_requested"]:
            raise SyncCancelled()

    def _save_mapping(self, run_id: int, pair: SyncPair, source_id: str, dest_id: str | None, state: str, **kwargs) -> None:
        previous = self.db.mapping(pair.id, source_id)
        self.db.record_change(run_id, "mapping", source_id, dict(previous) if previous else None)
        self.db.save_mapping(pair.id, source_id, dest_id, state, **kwargs)

    def _modify(self, run_id: int, pair: SyncPair, song_id: str, action: str) -> None:
        self._check_cancel(run_id)
        self.db.record_change(run_id, action, song_id)
        if action == "add":
            self.netease.add(pair.netease_playlist, song_id)
        else:
            self.netease.remove(pair.netease_playlist, song_id)
        self._check_cancel(run_id)

    def _run(self, pair: SyncPair, lock: threading.Lock, run_id: int) -> SyncResult:
        result = SyncResult(run_id, "running")
        try:
            self._check_cancel(run_id)
            source_tracks = self.apple.tracks(pair.apple_playlist)
            self._check_cancel(run_id)
            self.db.update_run(run_id, total=len(source_tracks))
            source_ids = {track.id for track in source_tracks}
            destination = {track.id: track for track in self.netease.playlist_tracks(pair.netease_playlist)}
            state = self.db.state(pair.id)
            initial_done = bool(state and state["initial_done"])
            unresolved = False
            for index, source in enumerate(source_tracks):
                self._check_cancel(run_id)
                self.db.update_run(run_id, current_source_id=source.id, current_source_title=f"{source.title} — {source.artist}")
                try:
                    mapping = self.db.mapping(pair.id, source.id)
                    if mapping and mapping["state"] == "incorrect":
                        unresolved = True
                        result.ambiguous += 1
                        continue
                    if mapping and mapping["state"] == "synced":
                        if mapping["manual"]:
                            if mapping["dest_id"] not in destination:
                                self._modify(run_id, pair, mapping["dest_id"], "add")
                                destination[mapping["dest_id"]] = self.netease.song(mapping["dest_id"])
                                result.added += 1
                            result.skipped += 1
                            continue
                        if mapping["dest_id"] in destination:
                            result.skipped += 1
                            continue
                    self.db.set_source(pair.id, source)
                    source.audio_locale = self.apple.audio_locale(source)
                    storefront = storefront_for_locale(source.audio_locale)
                    self._check_cancel(run_id)
                    try:
                        canonical = self.apple.metadata(source, storefront)
                    except Exception:
                        logger.warning("Apple metadata lookup failed; retrying CN: pair=%s source=%s storefront=%s", pair.id, source.id, storefront, exc_info=True)
                        canonical = self.apple.metadata(source, "cn")
                        canonical.raw["fallback_storefront"] = True
                    self._check_cancel(run_id)
                    self.db.set_source(pair.id, canonical)
                    candidates = self.netease.search(f"{canonical.title} {canonical.artist}")
                    self._check_cancel(run_id)
                    match, ranked = choose(canonical, candidates)
                    method = "metadata"
                    reason = "no confident unique match"
                    if not match:
                        match, reason = self._match_lyrics(canonical, ranked[:10], run_id)
                        method = "lyrics"
                    self._check_cancel(run_id)
                    if not match:
                        logger.warning("No confident unique Netease match: pair=%s source=%s candidates=%s", pair.id, source.id, len(ranked))
                        unresolved = True
                        result.ambiguous += 1
                        self._save_mapping(run_id, pair, source.id, None, "ambiguous", error=reason, candidates=[self._candidate_json(item) for item in ranked[:10]])
                        self.notifier.send("Netease match failed", {"pair": pair.name, "title": canonical.title, "artist": canonical.artist, "storefront": canonical.storefront or storefront, "reason": reason})
                        continue
                    if match.id not in destination:
                        self._modify(run_id, pair, match.id, "add")
                        # Confirm the provider applied the write before saving a successful mapping.
                        actual = {track.id for track in self.netease.playlist_tracks(pair.netease_playlist)}
                        if match.id not in actual:
                            raise RuntimeError("Netease did not confirm the song in the destination playlist")
                        destination[match.id] = match
                        result.added += 1
                        self.notifier.send("Song added to Netease", {"pair": pair.name, "source": f"{canonical.title} — {canonical.artist}", "storefront": canonical.storefront or storefront, "netease": match.url or match.id})
                    self._check_cancel(run_id)
                    self._save_mapping(run_id, pair, source.id, match.id, "synced", destination=self._candidate_json(match), match_method=method)
                except SyncCancelled:
                    raise
                except Exception as exc:
                    logger.exception("Track sync failed: run=%s pair=%s source=%s", run_id, pair.id, source.id)
                    unresolved = True
                    result.failed += 1
                    self._save_mapping(run_id, pair, source.id, None, "failed", error=str(exc))
                    self.notifier.send("Song sync failed", {"pair": pair.name, "source": f"{source.title} — {source.artist}", "error": str(exc)})
                finally:
                    self.db.update_run(run_id, processed=index + 1, added=result.added, skipped=result.skipped,
                                       removed=result.removed, failed=result.failed, ambiguous=result.ambiguous)
            self._check_cancel(run_id)
            if pair.removal_policy == "mirror" and (initial_done or pair.initial_policy == "mirror") and not unresolved:
                mapped = self.db.mappings(pair.id)
                if not initial_done and pair.initial_policy == "mirror":
                    keep = {row["dest_id"] for row in mapped if row["source_id"] in source_ids and row["state"] == "synced" and row["dest_id"]}
                    for destination_id in list(destination):
                        if destination_id not in keep:
                            self._modify(run_id, pair, destination_id, "remove")
                            destination.pop(destination_id)
                            result.removed += 1
                for mapping in mapped:
                    if mapping["source_id"] not in source_ids:
                        other = [row for row in mapped if row["dest_id"] == mapping["dest_id"] and row["source_id"] in source_ids]
                        if mapping["dest_id"] and not other and mapping["dest_id"] in destination:
                            self._modify(run_id, pair, mapping["dest_id"], "remove")
                            destination.pop(mapping["dest_id"])
                            result.removed += 1
                        self._check_cancel(run_id)
                        self.db.record_change(run_id, "mapping", mapping["source_id"], dict(mapping))
                        with self.db.lock, self.db.conn:
                            self.db.conn.execute("DELETE FROM song_mappings WHERE pair_id = ? AND source_id = ?", (pair.id, mapping["source_id"]))
            with self.db.lock:
                self._check_cancel(run_id)
                if not unresolved and not initial_done:
                    self.db.set_initial_done(pair.id, True)
                self.db.set_last_success(pair.id)
                result.status = "partial" if unresolved else "success"
                self.db.finish_run(run_id, result.status, current_source_id=None, current_source_title=None, added=result.added, skipped=result.skipped, removed=result.removed, failed=result.failed, ambiguous=result.ambiguous)
            return result
        except SyncCancelled:
            result.status = self._rollback(pair, run_id)
            return result
        except Exception as exc:
            logger.exception("Playlist sync failed: run=%s pair=%s", run_id, pair.id)
            if self.db.run(run_id)["cancel_requested"]:
                result.status = self._rollback(pair, run_id)
            else:
                result.status, result.error = "failed", str(exc)
                self.db.finish_run(run_id, "failed", error=str(exc), added=result.added, skipped=result.skipped, removed=result.removed, failed=result.failed, ambiguous=result.ambiguous)
            return result
        finally:
            lock.release()

    def stop_and_revert(self, pair: SyncPair, run_id: int) -> None:
        row = self.db.run(run_id)
        if not row or row["pair_id"] != pair.id:
            raise ValueError("Run does not belong to this playlist connection")
        latest = self.db.latest_run(pair.id)
        if latest and latest["id"] != run_id:
            raise ValueError("Cannot revert an older run after a newer sync has started")
        if self.db.request_stop(run_id):
            return
        if row and row["status"] == "reverting":
            return
        if not row or row["status"] not in {"rollback_failed", "interrupted"}:
            raise ValueError("This run is no longer running")
        lock = self._locks.setdefault(pair.id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise ValueError("Wait for the active sync to finish before retrying revert")
        self.db.update_run(run_id, status="reverting", cancel_requested=1)
        def retry():
            try:
                self._rollback(pair, run_id)
            finally:
                lock.release()
        try:
            threading.Thread(target=retry, daemon=True).start()
        except Exception:
            lock.release()
            self.db.update_run(run_id, status="rollback_failed", error="Could not start revert worker")
            raise

    def _rollback(self, pair: SyncPair, run_id: int) -> str:
        self.db.update_run(run_id, status="reverting", current_source_title="Reverting changes from this run")
        errors = []
        changes = self.db.changes(run_id)
        for change in changes:
            try:
                target = change["target_id"]
                if change["action"] == "mapping":
                    previous = json.loads(change["previous_json"]) if change["previous_json"] else None
                    self.db.restore_mapping(pair.id, target, previous)
                else:
                    current = {track.id for track in self.netease.playlist_tracks(pair.netease_playlist)}
                    if change["action"] == "add" and target in current:
                        self.netease.remove(pair.netease_playlist, target)
                    elif change["action"] == "remove" and target not in current:
                        self.netease.add(pair.netease_playlist, target)
                    actual = {track.id for track in self.netease.playlist_tracks(pair.netease_playlist)}
                    if (target in actual) != (change["action"] == "remove"):
                        raise RuntimeError("Netease did not confirm the revert")
                self.db.mark_undone(change["id"])
            except Exception as exc:
                logger.exception("Run revert failed: run=%s action=%s target=%s", run_id, change["action"], change["target_id"])
                errors.append(f"{change['action']} {change['target_id']}: {exc}")
        status = "rollback_failed" if errors else "cancelled"
        self.db.finish_run(run_id, status, current_source_id=None, current_source_title=None, error="; ".join(errors) or None)
        return status

    def _match_lyrics(self, source: Track, candidates: list[Candidate], run_id: int | None = None) -> tuple[Candidate | None, str]:
        reason = "manual correction required: "
        if not candidates:
            return None, reason + "no Netease candidates"
        try:
            lines = [line.strip() for line in self.apple.lyrics(source).splitlines() if line.strip()][:3]
        except Exception:
            logger.exception("Apple lyrics unavailable: source=%s", source.id)
            return None, reason + "Apple lyrics lookup failed"
        if not lines:
            return None, reason + "Apple lyrics unavailable"
        matches = {}
        unavailable = []
        for candidate in candidates:
            if run_id is not None:
                self._check_cancel(run_id)
            try:
                candidate_lyrics = self.netease.lyrics(candidate.id)
                if any(contains_lyric_line(line, candidate_lyrics) for line in lines):
                    matches[candidate.id] = candidate
            except Exception:
                logger.exception("Netease lyrics unavailable: source=%s candidate=%s", source.id, candidate.id)
                unavailable.append(candidate.id)
        if len(matches) == 1:
            match = next(iter(matches.values()))
            logger.info("Lyric match verified: source=%s destination=%s", source.id, match.id)
            return match, ""
        if len(matches) > 1:
            match = max(matches.values(), key=lambda candidate: candidate.score)
            if match.score >= 0.90:
                logger.info("Lyric tie resolved by metadata: source=%s destination=%s score=%.4f", source.id, match.id, match.score)
                return match, ""
        detail = "multiple candidates contain one of the first three Apple lyric lines" if matches else "no candidate contains any of the first three Apple lyric lines"
        if unavailable:
            detail += "; lyric lookup failed for " + ", ".join(unavailable)
        return None, reason + detail

    def correct(self, pair: SyncPair, source_id: str, netease_id: str) -> None:
        lock = self._locks.setdefault(pair.id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise ValueError("Stop the active sync before correcting songs")
        try:
            mapping = self.db.mapping(pair.id, source_id)
            song = self.netease.song(netease_id)
            current = {track.id for track in self.netease.playlist_tracks(pair.netease_playlist)}
            if netease_id not in current:
                self.netease.add(pair.netease_playlist, netease_id)
            if netease_id not in {track.id for track in self.netease.playlist_tracks(pair.netease_playlist)}:
                raise RuntimeError("Netease did not confirm the corrected song")
            if mapping and mapping["dest_id"] and mapping["dest_id"] != netease_id:
                shared = any(row["source_id"] != source_id and row["dest_id"] == mapping["dest_id"] for row in self.db.mappings(pair.id))
                if not shared and mapping["dest_id"] in current:
                    self.netease.remove(pair.netease_playlist, mapping["dest_id"])
            self.db.save_mapping(pair.id, source_id, netease_id, "synced", manual=True, destination=self._candidate_json(song), match_method="manual")
            self.notifier.send("Manual Netease correction applied", {"pair": pair.name, "source_id": source_id, "netease": f"https://music.163.com/#/song?id={netease_id}"})
        finally:
            lock.release()

    def flag_incorrect(self, pair: SyncPair, source_id: str) -> None:
        lock = self._locks.setdefault(pair.id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise ValueError("Stop the active sync before flagging songs")
        try:
            mapping = self.db.mapping(pair.id, source_id)
            if not mapping:
                raise ValueError("Mapping not found")
            with self.db.lock, self.db.conn:
                self.db.conn.execute("UPDATE song_mappings SET state = 'incorrect', last_error = 'Flagged as incorrect; provide the correct Netease song URL' WHERE pair_id = ? AND source_id = ?", (pair.id, source_id))
        finally:
            lock.release()

    @staticmethod
    def _candidate_json(candidate: Candidate) -> dict[str, object]:
        return {"id": candidate.id, "title": candidate.title, "artist": candidate.artist, "album": candidate.album, "score": candidate.score}
