import httpx
import pytest

from music_sync.providers import AppleMusicClient


def test_rejected_apple_credentials_have_actionable_error(monkeypatch):
    def reject(url, **kwargs):
        assert kwargs["headers"]["Authorization"] == "Bearer developer-secret"
        assert kwargs["headers"]["Music-User-Token"] == "user-secret"
        return httpx.Response(401, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", reject)
    with pytest.raises(RuntimeError, match="authorize Apple Music again") as failure:
        AppleMusicClient("developer-secret", "user-secret").tracks("playlist")
    assert "developer-secret" not in str(failure.value)
    assert "user-secret" not in str(failure.value)


def test_storefront_uses_singular_endpoint_and_apple_web_origin(monkeypatch):
    import base64
    import json
    claims = {"iss": "AMPWebPlay", "root_https_origin": ["apple.com"]}
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")

    def success(url, **kwargs):
        assert url == "https://api.music.apple.com/v1/me/storefront"
        assert kwargs["headers"]["Origin"] == "https://music.apple.com"
        assert kwargs["headers"]["Referer"] == "https://music.apple.com/"
        assert kwargs["headers"]["Media-User-Token"] == "test-user-token"
        return httpx.Response(200, request=httpx.Request("GET", url), json={"data": [{"id": "us", "type": "storefronts"}]})

    monkeypatch.setattr(httpx, "get", success)
    assert AppleMusicClient(f"header.{payload}.signature", "test-user-token").storefront() == "us"


def test_missing_storefront_is_reported(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: httpx.Response(200, request=httpx.Request("GET", args[0]), json={"data": []}))
    with pytest.raises(RuntimeError, match="no storefront country code"):
        AppleMusicClient("test-developer-token", "test-user-token").storefront()


@pytest.mark.parametrize("playlist,path", [
    ("https://music.apple.com/us/playlist/example/pl.u-example", "/v1/catalog/us/playlists/pl.u-example/tracks"),
    ("pl.example", "/v1/catalog/us/playlists/pl.example/tracks"),
    ("p.example", "/v1/me/library/playlists/p.example/tracks"),
])
def test_playlist_scope_and_pagination(monkeypatch, playlist, path):
    calls = []

    def get(url, **kwargs):
        route = url.removeprefix("https://api.music.apple.com")
        calls.append(route)
        if route == "/v1/me/storefront":
            payload = {"data": [{"id": "us"}]}
        else:
            second_page = route.endswith("?offset=1")
            assert route == path + ("?offset=1" if second_page else "")
            payload = {"data": [{"id": "2" if second_page else "1", "attributes": {"name": "Song"}}]}
            if not second_page:
                payload["next"] = path + "?offset=1"
        return httpx.Response(200, request=httpx.Request("GET", url), json=payload)

    monkeypatch.setattr(httpx, "get", get)
    tracks = AppleMusicClient("developer", "user").tracks(playlist)
    assert [track.id for track in tracks] == ["1", "2"]
    assert calls == ["/v1/me/storefront", path, path + "?offset=1"]


def test_playlist_page_failure_does_not_return_partial_source(monkeypatch):
    def get(url, **kwargs):
        if url.endswith("/storefront"):
            payload = {"data": [{"id": "us"}]}
        elif "offset=" not in url:
            payload = {"data": [{"id": "1"}], "next": "/v1/catalog/us/playlists/pl.example/tracks?offset=1"}
        else:
            return httpx.Response(503, request=httpx.Request("GET", url))
        return httpx.Response(200, request=httpx.Request("GET", url), json=payload)

    monkeypatch.setattr(httpx, "get", get)
    with pytest.raises(httpx.HTTPStatusError):
        AppleMusicClient("developer", "user").tracks("pl.example")


def test_apple_lyrics_falls_back_and_parses_ttml(monkeypatch):
    from music_sync.models import Track
    client = AppleMusicClient('developer', 'user')
    calls = []
    def get(path, **kwargs):
        calls.append((path, kwargs['storefront']))
        if path.endswith('syllable-lyrics'):
            return {'data': []}
        return {'data': [{'attributes': {'ttml': '<tt xmlns="http://www.w3.org/ns/ttml"><head><metadata>Credit</metadata></head><body><div><p> </p><p><span>First </span><span>line</span></p><p>Second</p></div></body></tt>'}}]}
    monkeypatch.setattr(client, '_get', get)
    assert client.lyrics(Track('library', 'Song', 'Artist', storefront='jp', raw={'catalog_id': '123'})) == 'First line\nSecond'
    assert calls == [('/songs/123/syllable-lyrics', 'jp'), ('/songs/123/lyrics', 'jp')]


def test_lyrics_uses_apple_media_api(monkeypatch):
    from music_sync.models import Track
    def get(url, **kwargs):
        assert url == 'https://amp-api.music.apple.com/v1/catalog/jp/songs/123/syllable-lyrics'
        assert kwargs['headers']['Media-User-Token'] == 'user'
        return httpx.Response(200, request=httpx.Request('GET', url), json={'data': [{'attributes': {'ttml': '<tt><body><p>First line</p></body></tt>'}}]})
    monkeypatch.setattr(httpx, 'get', get)
    assert AppleMusicClient('developer', 'user').lyrics(Track('123', 'Song', 'Artist', storefront='jp')) == 'First line'


def test_lyrics_retries_original_source_storefront(monkeypatch):
    from music_sync.models import Track
    client = AppleMusicClient('developer', 'user')
    calls = []
    def get(path, **kwargs):
        calls.append(kwargs['storefront'])
        if kwargs['storefront'] == 'cn':
            return {'data': []}
        return {'data': [{'attributes': {'ttml': '<tt><body><p>First line</p></body></tt>'}}]}
    monkeypatch.setattr(client, '_get', get)
    assert client.lyrics(Track('123', 'Song', 'Artist', storefront='cn', raw={'storefront': 'us'})) == 'First line'
    assert calls == ['cn', 'cn', 'us']


def test_missing_audio_locale_is_explicitly_requested_from_media_api(monkeypatch):
    from music_sync.models import Track
    calls = []
    def get(url, **kwargs):
        calls.append(url)
        assert url == 'https://amp-api.music.apple.com/v1/catalog/us/songs/123'
        assert kwargs['params'] == {'fields': 'audioLocale'}
        return httpx.Response(200, request=httpx.Request('GET', url), json={'data': [{'attributes': {'audioLocale': 'ja'}}]})
    monkeypatch.setattr(httpx, 'get', get)
    client = AppleMusicClient('developer', 'user')
    assert client.audio_locale(Track('i.library', 'Song', 'Artist', raw={'catalog_id': '123', 'storefront': 'us'})) == 'ja'
    assert client.audio_locale(Track('123', 'Song', 'Artist', audio_locale='ja')) == 'ja'
    assert len(calls) == 1


def test_cn_unavailable_song_uses_taiwan_metadata_after_empty_isrc(monkeypatch):
    from music_sync.models import Track
    client = AppleMusicClient('developer', 'user')
    calls = []
    def get(path, **kwargs):
        calls.append((path, kwargs['storefront']))
        if kwargs['storefront'] == 'cn':
            if path != '/songs':
                raise RuntimeError('404')
            return {'data': []}
        return {'data': [{'id': '1762980607', 'attributes': {'name': '才二十三', 'artistName': '方大同'}}]}
    monkeypatch.setattr(client, '_get', get)
    source = Track('1762980607', '才二十三', 'Khalil Fong', isrc='HKC382400005', audio_locale='zh-Hans-CN', raw={'storefront': 'us'})
    result = client.metadata(source, 'cn')
    assert result.artist == '方大同'
    assert result.storefront == 'tw'
    assert result.audio_locale == 'zh-Hans-CN'
    assert calls == [('/songs/1762980607', 'cn'), ('/songs', 'cn'), ('/songs/1762980607', 'tw')]


def test_empty_metadata_raises_useful_error_after_bounded_fallbacks(monkeypatch):
    from music_sync.models import Track
    client = AppleMusicClient('developer', 'user')
    calls = []
    def get(path, **kwargs):
        calls.append(kwargs['storefront'])
        return {'data': []}
    monkeypatch.setattr(client, '_get', get)
    with pytest.raises(RuntimeError, match='metadata unavailable.*cn, tw, hk, us'):
        client.metadata(Track('missing', 'Song', 'Artist', raw={'storefront': 'us'}), 'cn')
    assert calls == ['cn', 'tw', 'hk', 'us']
