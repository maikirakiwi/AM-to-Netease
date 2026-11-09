import base64
import copy
import json
import time
from types import SimpleNamespace

import httpx
import pytest

from music_sync import apple_token
from music_sync.scheduler import SchedulerService
from music_sync.settings import DEFAULT_SETTINGS, SettingsStore


def jwt(**overrides):
    claims = {"iss": "AMPWebPlay", "exp": time.time() + 3600, "root_https_origin": ["apple.com"], **overrides}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJFUzI1NiJ9.{payload}.signature"


def mock_player(monkeypatch, bundle, html='<script type="module" src="/assets/index~new-hash.js"></script>'):
    urls = []
    def respond(request):
        urls.append(str(request.url))
        assert request.headers['cache-control'] == 'no-cache'
        return httpx.Response(200, text=html if request.url.path == '/us/new' else bundle)
    client = httpx.Client
    monkeypatch.setattr(apple_token.httpx, 'Client', lambda **kwargs: client(transport=httpx.MockTransport(respond), **kwargs))
    return urls


def test_discovers_current_bundle_and_selects_valid_web_token(monkeypatch):
    token = jwt()
    urls = mock_player(monkeypatch, '\n'.join([jwt(iss='other'), jwt(exp=0), 'eyJbad.bad.bad', token]))
    assert apple_token.fetch_developer_token() == token
    assert urls == ['https://music.apple.com/us/new', 'https://music.apple.com/assets/index~new-hash.js']


@pytest.mark.parametrize('html,bundle', [
    ('<script src="https://evil.example/assets/index~x.js"></script>', ''),
    ('<script src="/assets/index~x.js"></script>', jwt(exp=0)),
    ('<script src="/assets/index~x.js"></script>', jwt(root_https_origin=['other.com'])),
])
def test_rejects_missing_expired_or_wrong_origin_tokens(monkeypatch, html, bundle):
    urls = mock_player(monkeypatch, bundle, html)
    with pytest.raises(RuntimeError, match='No valid Apple Music'):
        apple_token.fetch_developer_token()
    assert all(url.startswith('https://music.apple.com/') for url in urls)


@pytest.mark.parametrize('sync_time,refresh_time', [('03:00', (2, 50)), ('00:05', (23, 55))])
def test_refresh_job_uses_sync_timezone_and_handles_midnight(tmp_path, sync_time, refresh_time):
    settings = SettingsStore(tmp_path / 'settings.json')
    config = copy.deepcopy(DEFAULT_SETTINGS)
    config['schedule'].update(time=sync_time, timezone='America/New_York')
    settings.save(config)
    callback = lambda: None
    service = SchedulerService(settings, None, callback)
    service._configure(config)
    job = service.scheduler.get_job('apple-token-refresh')
    assert job.func == callback
    assert str(job.trigger.timezone) == 'America/New_York'
    fields = {field.name: str(field) for field in job.trigger.fields}
    assert (int(fields['hour']), int(fields['minute'])) == refresh_time


def test_refresh_persists_token_and_updates_live_client_without_rebuilding(tmp_path, monkeypatch):
    from music_sync import app as web
    runtime = web.Runtime.__new__(web.Runtime)
    runtime.settings = SettingsStore(tmp_path / 'settings.json')
    config = copy.deepcopy(DEFAULT_SETTINGS)
    config['apple'].update(developer_token='old', music_user_token='user')
    runtime.settings.save(config)
    runtime.sync = SimpleNamespace(apple=web.AppleMusicClient('old', 'user'))
    token = jwt()
    monkeypatch.setattr(web, 'fetch_developer_token', lambda: token)
    runtime.refresh_apple_token()
    assert runtime.settings.load()['apple'] == {'developer_token': token, 'music_user_token': 'user'}
    assert runtime.settings.path.stat().st_mode & 0o777 == 0o600
    assert runtime.sync.apple.developer_token == token
    assert runtime.sync.apple.origin == 'https://music.apple.com'
    previous = runtime.sync.apple
    def fail():
        raise RuntimeError('network unavailable')
    monkeypatch.setattr(web, 'fetch_developer_token', fail)
    runtime.refresh_apple_token()
    assert runtime.settings.load()['apple']['developer_token'] == token
    assert runtime.sync.apple is previous


def test_launch_refreshes_before_scheduler_starts(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from music_sync import app as web
    events = []
    runtime = SimpleNamespace(
        refresh_apple_token=lambda: events.append('refresh'),
        scheduler=SimpleNamespace(start=lambda: events.append('start'), stop=lambda: events.append('stop')),
    )
    monkeypatch.setattr(web, 'runtime', runtime)
    with TestClient(web.app):
        assert events == ['refresh', 'start']
    assert events == ['refresh', 'start', 'stop']


def test_settings_save_preserves_automatic_token(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from music_sync import app as web
    settings = SettingsStore(tmp_path / 'settings.json')
    config = copy.deepcopy(DEFAULT_SETTINGS)
    config['apple']['developer_token'] = 'automatic'
    settings.save(config)
    monkeypatch.setattr(web, 'runtime', SimpleNamespace(refresh_apple_token=lambda: None, scheduler=None, settings=settings, rebuild=lambda: None))
    with TestClient(web.app) as client:
        response = client.post('/settings', data={'schedule_time': '00:05', 'timezone': 'Asia/Shanghai'}, follow_redirects=False)
    assert response.status_code == 303
    assert settings.load()['apple']['developer_token'] == 'automatic'


def test_setup_fetches_token_without_prompting_for_it(tmp_path, monkeypatch):
    from music_sync import cli, providers
    answers = iter(['', '', '', 'n'])
    monkeypatch.setattr('builtins.input', lambda prompt: next(answers))
    monkeypatch.setattr(cli, 'fetch_developer_token', lambda: 'automatic')
    monkeypatch.setattr(providers, 'PyncmClient', lambda: SimpleNamespace(login_qr=lambda: {'session': 'saved'}))
    settings = SettingsStore(tmp_path / 'settings.json')
    cli.setup(settings)
    assert settings.load()['apple']['developer_token'] == 'automatic'
    assert settings.load()['netease']['session'] == {'session': 'saved'}


def test_scheduler_refresh_runs_before_account_is_configured_and_reloads_time(tmp_path):
    settings = SettingsStore(tmp_path / 'settings.json')
    config = copy.deepcopy(DEFAULT_SETTINGS)
    settings.save(config)
    service = SchedulerService(settings, None, lambda: None)
    try:
        service.start()
        assert [job.id for job in service.scheduler.get_jobs()] == ['apple-token-refresh']
        config['schedule']['time'] = '00:05'
        settings.save(config)
        service.reload()
        assert "hour='23', minute='55'" in str(service.scheduler.get_job('apple-token-refresh').trigger)
        config['schedule']['enabled'] = False
        settings.save(config)
        service.reload()
        assert service.scheduler.get_jobs() == []
    finally:
        service.stop()
