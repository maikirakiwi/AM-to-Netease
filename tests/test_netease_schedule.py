import copy
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from music_sync.models import SyncPair
from music_sync.scheduler import SchedulerService
from music_sync.settings import DEFAULT_SETTINGS, SettingsStore


@pytest.mark.parametrize('sync_time,expected', [('03:00', (2, 50)), ('00:05', (23, 55))])
def test_netease_refresh_schedule_timezone_and_midnight(tmp_path, sync_time, expected):
    settings = SettingsStore(tmp_path / 'settings.json')
    config = copy.deepcopy(DEFAULT_SETTINGS)
    config['schedule'].update(time=sync_time, timezone='America/New_York')
    settings.save(config)
    callback = lambda: None
    service = SchedulerService(settings, None, lambda: None, callback)
    service._configure(config)
    job = service.scheduler.get_job('netease-session-refresh')
    fields = {field.name: str(field) for field in job.trigger.fields}
    assert (int(fields['hour']), int(fields['minute'])) == expected
    assert str(job.trigger.timezone) == 'America/New_York'
    assert job.func is callback


def test_daily_job_runs_enabled_pairs_in_order_and_catchup_is_one_batch(tmp_path, monkeypatch):
    import music_sync.scheduler as module
    settings = SettingsStore(tmp_path / 'settings.json')
    config = copy.deepcopy(DEFAULT_SETTINGS)
    config['schedule'].update(time='03:00', timezone='Asia/Shanghai')
    settings.save(config)
    for name, enabled in [('first', True), ('disabled', False), ('last', True)]:
        settings.upsert_pair(SyncPair(name, name, 'apple', 'netease', enabled=enabled))
    calls = []
    sync = SimpleNamespace(sync=lambda pair: calls.append(pair.id), db=SimpleNamespace(state=lambda _: {'last_success': '2026-11-17T00:00:00+00:00'}))
    service = SchedulerService(settings, sync, lambda: None)
    service._configure(config)
    daily = service.scheduler.get_job('daily-sync')
    assert not service.scheduler.get_job('first')
    daily.func()
    assert calls == ['first', 'last']
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2026, 11, 18, 12, tzinfo=timezone.utc).astimezone(tz)
    monkeypatch.setattr(module, 'datetime', Clock)
    service._catch_up(config)
    catchup = service.scheduler.get_job('catchup-sync')
    assert catchup.args == (['first', 'last'],)
    calls.clear()
    catchup.func(*catchup.args)
    assert calls == ['first', 'last']


def test_refresh_persists_session_and_failure_preserves_saved_credentials(tmp_path, caplog):
    import threading
    from music_sync import app as web
    runtime = web.Runtime.__new__(web.Runtime)
    runtime.settings = SettingsStore(tmp_path / 'settings.json')
    config = copy.deepcopy(DEFAULT_SETTINGS)
    config['netease']['session'] = {'old': 'session'}
    runtime.settings.save(config)
    runtime.sync = SimpleNamespace(_sync_lock=threading.RLock(), netease=SimpleNamespace(refresh_session=lambda: {'renewed': 'session'}))
    runtime.refresh_netease_session()
    assert runtime.settings.load()['netease']['session'] == {'renewed': 'session'}
    assert runtime.settings.path.stat().st_mode & 0o777 == 0o600
    def fail():
        raise RuntimeError('secret-cookie-must-not-be-logged')
    runtime.sync.netease.refresh_session = fail
    runtime.refresh_netease_session()
    assert runtime.settings.load()['netease']['session'] == {'renewed': 'session'}
    assert 'QR re-login' in caplog.text
    assert 'secret-cookie-must-not-be-logged' not in caplog.text
