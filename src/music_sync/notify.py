from __future__ import annotations

from typing import Any

try:
    import httpx
except ImportError:  # pragma: no cover - used only in dependency-free smoke checks
    httpx = None  # type: ignore


class DiscordNotifier:
    def __init__(self, webhook_url: str = "") -> None:
        self.webhook_url = webhook_url

    def send(self, content: str, fields: dict[str, Any] | None = None) -> None:
        if not self.webhook_url:
            return
        embed = {"title": content, "fields": [{"name": str(key), "value": str(value)[:1000], "inline": False} for key, value in (fields or {}).items()]}
        try:
            if httpx is not None:
                httpx.post(self.webhook_url, json={"embeds": [embed]}, timeout=10)
            else:
                import json
                from urllib.request import Request, urlopen
                request = Request(self.webhook_url, data=json.dumps({"embeds": [embed]}).encode(), headers={"Content-Type": "application/json"})
                with urlopen(request, timeout=10):  # nosec B310 - configured Discord webhook
                    pass
        except Exception:
            pass
