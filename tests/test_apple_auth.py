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
