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
        return httpx.Response(200, request=httpx.Request("GET", url), json={"data": [{"id": "us", "type": "storefronts"}]})

    monkeypatch.setattr(httpx, "get", success)
    assert AppleMusicClient(f"header.{payload}.signature", "test-user-token").storefront() == "us"


def test_missing_storefront_is_reported(monkeypatch):
    monkeypatch.setattr(httpx, "get", lambda *args, **kwargs: httpx.Response(200, request=httpx.Request("GET", args[0]), json={"data": []}))
    with pytest.raises(RuntimeError, match="no storefront country code"):
        AppleMusicClient("test-developer-token", "test-user-token").storefront()
