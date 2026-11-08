import threading
import time
from types import SimpleNamespace

from fastapi.testclient import TestClient

from music_sync.database import SyncDatabase
from music_sync.models import SyncPair
from music_sync.sync import SyncService
from test_sync import FakeApple, FakeNetease


def test_background_run_redirects_and_reports_progress_without_blocking(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    from music_sync import app as web
    apple, netease = FakeApple(), FakeNetease()
    entered, release = threading.Event(), threading.Event()
    original = apple.metadata
    def metadata(track, storefront):
        entered.set()
        assert release.wait(5)
        return original(track, storefront)
    apple.metadata = metadata
    db = SyncDatabase(tmp_path / 'web.sqlite3')
    service = SyncService(db, apple, netease)
    pair = SyncPair('pair', 'Pair', 'apple', 'netease')
    monkeypatch.setattr(web, 'runtime', SimpleNamespace(db=db, sync=service, scheduler=None, settings=SimpleNamespace(pairs=lambda: [pair])))
    with TestClient(web.app) as client:
        try:
            response = client.post('/pairs/pair/sync', follow_redirects=False)
            assert response.status_code == 303
            assert entered.wait(2)
            location = response.headers['location']
            run_id = int(location.rsplit('/', 1)[1])
            status = client.get(f'/api/runs/{run_id}').json()
            assert status['status'] == 'running'
            assert status['total'] == status['remaining'] == 1
            assert status['processed'] == 0
            assert status['current_source_title'] == 'Song — Artist'
            assert client.get(location).status_code == 200
            assert '<progress' in client.get(location).text
            assert client.get('/healthz').status_code == 200
            duplicate = client.post('/pairs/pair/sync', follow_redirects=False)
            assert duplicate.headers['location'] == location
            assert len(db.recent_runs()) == 1
            assert client.get('/api/runs/999').status_code == 404
            assert client.get('/runs/999').status_code == 404
        finally:
            release.set()
        deadline = time.monotonic() + 3
        while db.run(run_id)['status'] == 'running' and time.monotonic() < deadline:
            time.sleep(0.01)
        status = client.get(f'/api/runs/{run_id}').json()
        assert status['status'] == 'success'
        assert status['processed'] == status['added'] == 1
        assert status['remaining'] == 0


def test_dashboard_song_breakdowns_filters_and_packaged_assets(tmp_path, monkeypatch):
    import copy
    from music_sync import app as web
    from music_sync.models import Track
    from music_sync.settings import DEFAULT_SETTINGS, SettingsStore
    monkeypatch.chdir(tmp_path)
    settings = SettingsStore(tmp_path / 'settings.json')
    settings.save(copy.deepcopy(DEFAULT_SETTINGS))
    pair = SyncPair('pair', 'Japanese favorites', 'pl.example', '123')
    settings.upsert_pair(pair)
    db = SyncDatabase(tmp_path / 'ui.sqlite3')
    db.set_source('pair', Track('source', '<script>alert(1)</script>', 'Artist', 'Album', audio_locale='ja', storefront='jp'))
    db.save_mapping('pair', 'source', None, 'ambiguous', error='No unique lyric match', candidates=[{'id':'12','title':'Candidate','artist':'Artist','album':'Album','score':.8}])
    service = SyncService(db, FakeApple(), FakeNetease())
    monkeypatch.setattr(web, 'runtime', SimpleNamespace(db=db, sync=service, scheduler=None, settings=settings))
    with TestClient(web.app) as client:
        for route in ('/', '/playlists', '/songs', '/attention', '/activity', '/settings', '/setup/apple', '/pairs/pair/edit', '/pairs/pair/mappings'):
            response = client.get(route)
            assert response.status_code == 200, route
            assert '<script>alert(1)</script>' not in response.text
        assert '1 results' in client.get('/attention').text
        assert '0 results' in client.get('/songs?state=synced').text
        assert '0 results' in client.get('/songs?q=missing').text
        assert 'No unique lyric match' in client.get('/pairs/pair/mappings').text
        assert '80.0%' in client.get('/pairs/pair/mappings').text
        assert client.get('/songs?state=invalid').status_code == 400
        assert client.get('/api/runs/999').status_code == 404
        for asset in ('dashboard.css', 'dashboard.js', 'run_progress.js', 'apple_auth.js'):
            assert client.get('/static/' + asset).status_code == 200
        assert client.get('/static/providers.py').status_code == 404
        response = client.post('/settings', data={'schedule_time':'03:00','timezone':'not/a-zone'})
        assert response.status_code == 400


def test_stop_route_returns_immediately_and_reverts_after_provider_returns(tmp_path, monkeypatch):
    from music_sync import app as web
    apple, netease = FakeApple(), FakeNetease()
    entered, release = threading.Event(), threading.Event()
    original = apple.metadata
    def metadata(track, storefront):
        entered.set()
        assert release.wait(3)
        return original(track, storefront)
    apple.metadata = metadata
    db = SyncDatabase(tmp_path / 'stop-web.sqlite3')
    service = SyncService(db, apple, netease)
    pair = SyncPair('pair', 'Pair', 'apple', 'netease')
    monkeypatch.setattr(web, 'runtime', SimpleNamespace(db=db, sync=service, scheduler=None, settings=SimpleNamespace(pairs=lambda: [pair])))
    with TestClient(web.app) as client:
        try:
            location = client.post('/pairs/pair/sync', follow_redirects=False).headers['location']
            assert entered.wait(2)
            response = client.post(location + '/stop', follow_redirects=False)
            assert response.status_code == 303
            run_id = int(location.rsplit('/',1)[-1])
            assert client.get('/api/runs/' + str(run_id)).json()['status'] == 'stopping'
        finally:
            release.set()
        from test_sync import wait_for_run
        assert wait_for_run(db,run_id)['status'] == 'cancelled'
        assert netease.items == []
