from types import SimpleNamespace

from music_sync.providers import PyncmClient


def test_fork_session_and_api_calls(monkeypatch):
    client = PyncmClient()
    restored = PyncmClient(client.session())
    assert restored.session() == client.session()
    calls = []
    client.playlist_api = SimpleNamespace(
        GetPlaylistInfo=lambda playlist, **kwargs: {"playlist": {"trackIds": [{"id": 12}]}},
        SetManipulatePlaylistTracks=lambda ids, playlist, **kwargs: calls.append((ids, playlist, kwargs)) or {"code": 200},
    )
    client.track_api = SimpleNamespace(GetTrackDetail=lambda ids, **kwargs: {"songs": [{"id": ids[0], "name": "Song"}]})
    assert client.playlist_tracks("123")[0].id == "12"
    client.add("123", "12")
    assert calls[0][0:2] == (["12"], "123")
    assert calls[0][2]["session"] is client._session


def test_qr_login_uses_fork_flow(monkeypatch):
    from pyncm.apis import login
    import qrcode

    client = PyncmClient()
    monkeypatch.setattr(login, "LoginQrcodeUnikey", lambda **kwargs: {"unikey": "key"})
    monkeypatch.setattr(login, "GetLoginQRCodeUrl", lambda key, **kwargs: "https://example.com/qr")
    monkeypatch.setattr(login, "LoginQrcodeCheck", lambda key, **kwargs: {"code": 803})
    monkeypatch.setattr(login, "GetCurrentLoginStatus", lambda **kwargs: {})
    monkeypatch.setattr(client.pyncm, "WriteLoginInfo", lambda status, session: None)
    monkeypatch.setattr(qrcode.QRCode, "print_ascii", lambda *args, **kwargs: None)
    assert client.login_qr() == client.session()


def test_netease_lyrics_uses_session():
    client = PyncmClient()
    def lyrics(song, **kwargs):
        assert song == '123'
        assert kwargs['session'] is client._session
        return {'code': 200, 'lrc': {'lyric': '[00:01]First line'}}
    client.track_api = SimpleNamespace(GetTrackLyrics=lyrics)
    assert client.lyrics('123') == '[00:01]First line'


def test_search_returns_first_result_and_logs_query(caplog):
    import logging
    client = PyncmClient()
    def search(query, **kwargs):
        assert query == '七月七日晴 许慧欣'
        assert kwargs['session'] is client._session
        return {'code': 200, 'result': {'songs': [{'id': 307780, 'name': '七月七日晴', 'ar': [{'name': '许慧欣'}]}]}}
    client.search_api = SimpleNamespace(GetSearchResult=search)
    with caplog.at_level(logging.INFO):
        songs = client.search('七月七日晴 许慧欣')
    assert songs[0].id == '307780'
    assert songs[0].artist == '许慧欣'
    assert 'candidates=1' in caplog.text
    assert '七月七日晴 许慧欣' in caplog.text


def test_search_retries_empty_or_transient_response_once(monkeypatch):
    monkeypatch.setattr('music_sync.providers.time.sleep', lambda _: None)
    valid = {'code': 200, 'result': {'songs': [{'id': 307780, 'name': '七月七日晴'}]}}
    for first in ({'code': 405}, {'code': 429}, {'code': 503}, {'code': 200, 'result': {'songCount': 0}}):
        responses = iter([first, valid])
        client = PyncmClient()
        client.search_api = SimpleNamespace(GetSearchResult=lambda *a, **k: next(responses))
        assert client.search('query')[0].id == '307780'


def test_search_does_not_turn_provider_errors_into_zero_candidates(monkeypatch):
    import pytest
    monkeypatch.setattr('music_sync.providers.time.sleep', lambda _: None)
    for response in ({'code': 301}, {'code': 405}, {'code': 200}, {'code': 200, 'result': {}}, {'code': 200, 'result': {'songs': None}}, 'not json'):
        client = PyncmClient()
        calls = []
        def search(*args, **kwargs):
            calls.append(1)
            return response
        client.search_api = SimpleNamespace(GetSearchResult=search)
        with pytest.raises(RuntimeError, match='Netease search'):
            client.search('query')
        assert len(calls) <= 2


def test_search_supports_overseas_results_and_bounded_genuine_empty(monkeypatch):
    monkeypatch.setattr('music_sync.providers.time.sleep', lambda _: None)
    client = PyncmClient()
    client.search_api = SimpleNamespace(GetSearchResult=lambda *a, **k: {'abroad': True, 'result': {'songs': [{'id': 1}]}})
    assert client.search('query')[0].id == '1'
    calls = []
    def empty(*args, **kwargs):
        calls.append(1)
        return {'code': 200, 'result': {'songCount': 0}}
    client.search_api = SimpleNamespace(GetSearchResult=empty)
    assert client.search('query') == []
    assert len(calls) == 2
