from __future__ import annotations

from datetime import datetime, time
from collections.abc import Callable
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from .settings import SettingsStore
from .sync import SyncService


class SchedulerService:
    def __init__(self, settings: SettingsStore, sync: SyncService | None, refresh_token: Callable[[], None]) -> None:
        self.settings, self.sync = settings, sync
        self.refresh_token = refresh_token
        self.scheduler = BackgroundScheduler()

    def start(self) -> None:
        config = self.settings.load()
        if config["schedule"].get("enabled", True):
            self._configure(config)
            self.scheduler.start()
            if config["schedule"].get("catch_up", True):
                self._catch_up(config)

    def stop(self) -> None:
        if self.scheduler.running:
            self.scheduler.shutdown(wait=False)

    def reload(self) -> None:
        if self.scheduler.running:
            self.scheduler.remove_all_jobs()
            config = self.settings.load()
            if config["schedule"].get("enabled", True):
                self._configure(config)

    def _configure(self, config: dict) -> None:
        hour, minute = (int(value) for value in config["schedule"].get("time", "03:00").split(":", 1))
        timezone = ZoneInfo(config["schedule"].get("timezone", "Asia/Shanghai"))
        refresh_minute = (hour * 60 + minute - 10) % (24 * 60)
        self.scheduler.add_job(self.refresh_token, CronTrigger(hour=refresh_minute // 60, minute=refresh_minute % 60, timezone=timezone), id="apple-token-refresh", replace_existing=True, max_instances=1, coalesce=True, misfire_grace_time=600)
        if not self.sync:
            return
        for pair in self.settings.pairs():
            if pair.enabled:
                self.scheduler.add_job(self.sync.sync, CronTrigger(hour=hour, minute=minute, timezone=timezone), args=[pair], id=pair.id, replace_existing=True, max_instances=1, coalesce=True)

    def _catch_up(self, config: dict) -> None:
        if not self.sync:
            return
        hour, minute = (int(value) for value in config["schedule"].get("time", "03:00").split(":", 1))
        timezone = ZoneInfo(config["schedule"].get("timezone", "Asia/Shanghai"))
        current = datetime.now(timezone)
        if current.time() < time(hour, minute):
            return
        for pair in self.settings.pairs():
            state = self.sync.db.state(pair.id)
            if not pair.enabled or not state or not state["last_success"]:
                continue
            last = datetime.fromisoformat(state["last_success"]).astimezone(timezone)
            if last.date() < current.date():
                self.scheduler.add_job(self.sync.sync, args=[pair], id=f"catchup-{pair.id}", replace_existing=True, max_instances=1)
