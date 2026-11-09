from __future__ import annotations

import base64
import json
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

import httpx


class _Scripts(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.sources: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "script":
            source = dict(attrs).get("src")
            if source:
                self.sources.append(source)


def fetch_developer_token() -> str:
    # shortcut: Apple's public bundle layout is undocumented; update discovery if the player changes.
    with httpx.Client(timeout=30, follow_redirects=True, headers={"Cache-Control": "no-cache"}) as client:
        page = client.get("https://music.apple.com/us/new")
        page.raise_for_status()
        scripts = _Scripts()
        scripts.feed(page.text)
        for source in scripts.sources:
            url = urljoin(str(page.url), source)
            parts = urlsplit(url)
            if parts.scheme != "https" or parts.netloc != "music.apple.com" or not re.fullmatch(r"/assets/index[~.-][\w.-]+\.js", parts.path):
                continue
            bundle = client.get(url)
            bundle.raise_for_status()
            for token in re.findall(r"eyJ[\w-]+\.[\w-]+\.[\w-]+", bundle.text):
                try:
                    payload = token.split(".")[1]
                    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
                    if (claims.get("iss") == "AMPWebPlay"
                            and "apple.com" in claims.get("root_https_origin", [])
                            and claims["exp"] > time.time() + 600):
                        return token
                except (ValueError, TypeError, KeyError, AttributeError):
                    continue
    raise RuntimeError("No valid Apple Music web-player developer token found; Apple's player may have changed")
