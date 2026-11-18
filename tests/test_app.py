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
    monkeypatch.setattr(web, 'runtime', SimpleNamespace(refresh_netease_session=lambda: None, refresh_apple_token=lambda: None, db=db, sync=service, scheduler=None, settings=SimpleNamespace(pairs=lambda: [pair])))
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
    monkeypatch.setattr(web, 'runtime', SimpleNamespace(refresh_netease_session=lambda: None, refresh_apple_token=lambda: None, db=db, sync=service, scheduler=None, settings=settings))
    with TestClient(web.app) as client:
        for route in ('/', '/playlists', '/songs', '/attention', '/activity', '/settings', '/setup/apple', '/pairs/pair/edit', '/pairs/pair/mappings'):
            response = client.get(route)
            assert response.status_code == 200, route
            assert '<script>alert(1)</script>' not in response.text
        attention_page = client.get('/attention').text
        assert '1 results' in attention_page
        assert 'href="#song-pair-source"' in attention_page
        assert 'id="song-pair-source"' in attention_page
        assert 'No unique lyric match' in attention_page
        assert '80.0%' in attention_page
        assert 'action="/pairs/pair/mappings/source/correct"' in attention_page
        assert 'name="return_to" value="/attention"' in attention_page
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
    monkeypatch.setattr(web, 'runtime', SimpleNamespace(refresh_netease_session=lambda: None, refresh_apple_token=lambda: None, db=db, sync=service, scheduler=None, settings=SimpleNamespace(pairs=lambda: [pair])))
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


def test_attention_correction_returns_to_queue_and_preserves_other_review_routes(tmp_path, monkeypatch):
    from music_sync import app as web
    pair = SyncPair('pair', 'Pair', 'apple', 'netease')
    db = SyncDatabase(tmp_path / 'correction-web.sqlite3')
    calls = []
    service = SimpleNamespace(correct=lambda *args, **kwargs: calls.append((args, kwargs)) or 42)
    monkeypatch.setattr(web, 'runtime', SimpleNamespace(refresh_netease_session=lambda: None, refresh_apple_token=lambda: None, db=db, sync=service, scheduler=None, settings=SimpleNamespace(pairs=lambda: [pair])))
    with TestClient(web.app) as client:
        for return_to, expected in [('/attention', '/attention'), ('', '/pairs/pair/mappings'), ('https://example.com', '/pairs/pair/mappings')]:
            response = client.post('/pairs/pair/mappings/source/correct', data={'url': 'https://music.163.com/#/song?id=123', 'return_to': return_to}, follow_redirects=False)
            assert response.status_code == 303
            assert response.headers['location'] == expected + '?correction_run=42'
        assert calls == [((pair, 'source', '123'), {'background': True})] * 3


def test_manual_correction_returns_before_provider_and_dashboard_stays_responsive(tmp_path, monkeypatch):
    from music_sync import app as web
    from test_sync import wait_for_run
    apple, netease = FakeApple(), FakeNetease()
    db = SyncDatabase(tmp_path / 'background-correction.sqlite3')
    db.set_source('pair', apple.source)
    db.save_mapping('pair', apple.source.id, None, 'ambiguous')
    service = SyncService(db, apple, netease)
    pair = SyncPair('pair', 'Pair', 'apple', 'netease')
    entered, release = threading.Event(), threading.Event()
    original = netease.song
    def song(song_id):
        entered.set()
        assert release.wait(5)
        return original(song_id)
    netease.song = song
    monkeypatch.setattr(web, 'runtime', SimpleNamespace(refresh_netease_session=lambda: None, refresh_apple_token=lambda: None,
                                                     db=db, sync=service, scheduler=None, settings=SimpleNamespace(pairs=lambda: [pair])))
    with TestClient(web.app) as client:
        try:
            response = client.post('/pairs/pair/mappings/apple-1/correct', data={'url': 'https://music.163.com/#/song?id=123', 'return_to': '/attention'}, follow_redirects=False)
            assert response.status_code == 303
            assert entered.wait(2)
            run_id = int(response.headers['location'].split('=')[1])
            assert client.get('/api/runs/' + str(run_id)).json()['status'] == 'running'
            page = client.get(response.headers['location'])
            assert page.status_code == 200
            assert 'id="correction-status"' in page.text
            assert client.get('/healthz').status_code == 200
            assert client.get('/playlists').status_code == 200
            duplicate = client.post('/pairs/pair/mappings/apple-1/correct', data={'url': '123'}, follow_redirects=False)
            assert duplicate.status_code == 400
        finally:
            release.set()
        assert wait_for_run(db, run_id)['status'] == 'success'
        assert db.mapping('pair', 'apple-1')['dest_id'] == '123'
        assert '0 results' in client.get('/attention').text


def test_netease_qr_is_displayed_and_session_saved_only_after_confirmation(tmp_path, monkeypatch):
    import base64
    import copy
    import re
    from music_sync import app as web
    from music_sync.settings import DEFAULT_SETTINGS, SettingsStore
    settings = SettingsStore(tmp_path / 'settings.json')
    settings.save(copy.deepcopy(DEFAULT_SETTINGS))
    states = iter([801, 802, 803])
    rebuilds = []
    fake = SimpleNamespace(start_login_qr=lambda: ('private-key', 'https://music.163.com/login?codekey=private-key'),
                           check_login_qr=lambda key: next(states), session=lambda: {'cookie': 'secret'})
    monkeypatch.setattr(web, 'PyncmClient', lambda: fake)
    monkeypatch.setattr(web, '_netease_login', {})
    monkeypatch.setattr(web, 'runtime', SimpleNamespace(settings=settings, db=SyncDatabase(tmp_path / 'qr.sqlite3'), sync=None,
                      scheduler=None, _credential_lock=threading.RLock(), rebuild=lambda: rebuilds.append(True),
                      refresh_apple_token=lambda: None, refresh_netease_session=lambda: None))
    with TestClient(web.app) as client:
        page = client.post('/setup/netease')
        assert page.status_code == 200
        assert 'alt="Netease Music login QR code"' in page.text
        svg = base64.b64decode(re.search('data:image/svg\\+xml;base64,([^" ]+)', page.text)[1])
        assert b'<svg' in svg and b'<path' in svg
        attempt = re.search('data-attempt="([^"]+)"', page.text)[1]
        assert not settings.load()['netease']['session']
        for expected in ['waiting', 'scanned', 'authorized', 'authorized']:
            result = client.post(f'/setup/netease/{attempt}/check')
            assert result.json() == {'status': expected}
            assert 'secret' not in result.text
        assert settings.load()['netease']['session'] == {'cookie': 'secret'}
        assert rebuilds == [True]
        assert client.get('/static/netease_auth.js').status_code == 200
        assert client.post('/setup/netease/unknown/check').status_code == 410
        client.post('/setup/netease')
        assert client.post(f'/setup/netease/{attempt}/check').status_code == 410


def test_netease_qr_expiry_and_failure_keep_saved_session(tmp_path, monkeypatch):
    import copy
    from music_sync import app as web
    from music_sync.settings import DEFAULT_SETTINGS, SettingsStore
    settings = SettingsStore(tmp_path / 'settings.json')
    config = copy.deepcopy(DEFAULT_SETTINGS)
    config['netease']['session'] = {'old': 'session'}
    settings.save(config)
    monkeypatch.setattr(web, 'runtime', SimpleNamespace(settings=settings, refresh_apple_token=lambda: None,
                                                     refresh_netease_session=lambda: None, scheduler=None))
    def fail(key):
        raise RuntimeError('Provider error')
    with TestClient(web.app) as client:
        for code, expected in [(800, 'expired'), (None, 'failed')]:
            fake = SimpleNamespace(check_login_qr=(lambda key: code) if code else fail)
            monkeypatch.setattr(web, '_netease_login', dict(id='attempt', client=fake, key='key', status='waiting', expires=float('inf')))
            assert client.post('/setup/netease/attempt/check').json() == {'status': expected}
        monkeypatch.setattr(web, '_netease_login', dict(id='attempt', client=None, key='key', status='waiting', expires=0))
        assert client.post('/setup/netease/attempt/check').json() == {'status': 'expired'}
        assert settings.load()['netease']['session'] == {'old': 'session'}


def test_unavailable_option_removes_attention_and_is_visible_in_library(tmp_path, monkeypatch):
    import copy
    from music_sync import app as web
    from music_sync.settings import DEFAULT_SETTINGS, SettingsStore
    from test_sync import wait_for_run
    settings = SettingsStore(tmp_path / 'settings.json')
    settings.save(copy.deepcopy(DEFAULT_SETTINGS))
    pair = SyncPair('pair', 'Pair', 'apple', 'netease')
    settings.upsert_pair(pair)
    db = SyncDatabase(tmp_path / 'ignored-web.sqlite3')
    apple, netease = FakeApple(), FakeNetease()
    db.set_source(pair.id, apple.source)
    db.save_mapping(pair.id, apple.source.id, None, 'ambiguous')
    monkeypatch.setattr(web, 'runtime', SimpleNamespace(settings=settings, db=db, sync=SyncService(db, apple, netease),
                      scheduler=None, refresh_apple_token=lambda: None, refresh_netease_session=lambda: None))
    with TestClient(web.app) as client:
        assert 'Not available on Netease' in client.get('/attention').text
        response = client.post('/pairs/pair/mappings/apple-1/ignore', data={'return_to': '/attention'}, follow_redirects=False)
        assert response.status_code == 303
        assert response.headers['location'].startswith('/attention?correction_run=')
        assert wait_for_run(db, int(response.headers['location'].split('=')[1]))['status'] == 'success'
        assert '0 results' in client.get('/attention').text
        library = client.get('/songs?state=ignored')
        assert '1 results' in library.text and 'Not available' in library.text
        assert 'Apply correction' in client.get('/pairs/pair/mappings?state=ignored').text
        assert web.statistics(web.all_songs([pair]))['attention'] == 0
        assert web.pair_cards([pair], web.all_songs([pair]))[0]['attention'] == 0
        assert client.get('/').status_code == 200
