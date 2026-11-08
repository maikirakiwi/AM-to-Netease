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
