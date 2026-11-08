from __future__ import annotations

import re
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse

try:
    import httpx
except ImportError:  # pragma: no cover - used only in dependency-free smoke checks
    httpx = None  # type: ignore

from .models import Candidate, Track


LOCALE_STOREFRONTS = {
    "ja": "jp", "ko": "kr", "zh": "cn", "yue": "hk", "en": "us",
    "es": "es", "fr": "fr", "de": "de", "it": "it", "pt": "br",
    "ru": "ru", "th": "th", "vi": "vn",
}


def storefront_for_locale(locale: str | None) -> str:
    if not locale:
        return "cn"
    return LOCALE_STOREFRONTS.get(locale.replace("_", "-").split("-", 1)[0].lower(), "cn")


def playlist_id(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme:
        query_id = parse_qs(parsed.query).get("id", [None])[0]
        if query_id:
            return query_id
        segments = [segment for segment in parsed.path.split("/") if segment]
        for segment in reversed(segments):
            if segment.startswith("pl"):
                return segment
        if segments:
            return segments[-1]
    return value.strip()


def song_id(value: str) -> str:
    parsed = urlparse(value)
    query_id = parse_qs(parsed.query).get("id", [None])[0]
    if query_id:
        return query_id
    fragment_id = parse_qs(parsed.fragment.split("?", 1)[-1]).get("id", [None])[0]
    if fragment_id:
        return fragment_id
    match = re.search(r"/song/(\d+)", parsed.path)
    if match:
        return match.group(1)
    if value.strip().isdigit():
        return value.strip()
    raise ValueError("could not find a Netease song ID in the URL")


class AppleProvider(Protocol):
    def tracks(self, playlist: str) -> list[Track]: ...
    def metadata(self, track: Track, storefront: str) -> Track: ...


class NeteaseProvider(Protocol):
    def playlist_tracks(self, playlist: str) -> list[Candidate]: ...
    def search(self, query: str) -> list[Candidate]: ...
    def song(self, netease_id: str) -> Candidate: ...
    def add(self, playlist: str, netease_id: str) -> None: ...
    def remove(self, playlist: str, netease_id: str) -> None: ...


class AppleMusicClient:
    def __init__(self, developer_token: str, user_token: str, timeout: float = 30) -> None:
        self.developer_token = developer_token
        self.user_token = user_token
        self.timeout = timeout

    def _get(self, path: str, storefront: str | None = None, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if path.startswith("/v1/me"):
            url = f"https://api.music.apple.com{path}"
        else:
            url = f"https://api.music.apple.com/v1/catalog/{storefront or 'us'}{path}"
        headers = {"Authorization": f"Bearer {self.developer_token}", "Music-User-Token": self.user_token}
        if httpx is not None:
            response = httpx.get(url, headers=headers, params=params, timeout=self.timeout)
            response.raise_for_status()
            return response.json()
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
        if params:
            url += "?" + urlencode(params)
        with urlopen(Request(url, headers=headers), timeout=self.timeout) as response:  # nosec B310 - fixed HTTPS provider URL
            import json
            return json.load(response)

    def tracks(self, playlist: str) -> list[Track]:
        playlist_value = playlist_id(playlist)
        storefront = self._get("/v1/me/storefronts").get("data", [{"id": "us"}])[0]["id"]
        if playlist_value.startswith("pl.u-"):
            path = f"/v1/me/library/playlists/{playlist_value}/tracks"
            payload = self._get(path)
        else:
            payload = self._get(f"/playlists/{playlist_value}/tracks", storefront=storefront)
        tracks: list[Track] = []
        for item in payload.get("data", []):
            attrs = item.get("attributes", {})
            play_params = attrs.get("playParams", {})
            source_id = item.get("id", "")
            tracks.append(Track(
                id=source_id, title=attrs.get("name", ""), artist=attrs.get("artistName", ""),
                album=attrs.get("albumName", ""), duration_ms=attrs.get("durationInMillis"),
                isrc=attrs.get("isrc"), url=attrs.get("url"), audio_locale=attrs.get("audioLocale"),
                raw={"catalog_id": play_params.get("catalogId") or source_id, "storefront": storefront, "attributes": attrs},
            ))
        return tracks

    def metadata(self, track: Track, storefront: str) -> Track:
        catalog_id = track.raw.get("catalog_id") or track.id
        try:
            payload = self._get(f"/songs/{catalog_id}", storefront=storefront)
            item = payload["data"][0]
        except Exception:
            if track.isrc:
                payload = self._get("/songs", storefront=storefront, params={"filter[isrc]": track.isrc})
                item = payload["data"][0]
            else:
                raise
        attrs = item.get("attributes", {})
        return Track(
            id=track.id, title=attrs.get("name", track.title), artist=attrs.get("artistName", track.artist),
            album=attrs.get("albumName", track.album), duration_ms=attrs.get("durationInMillis", track.duration_ms),
            isrc=attrs.get("isrc", track.isrc), url=attrs.get("url", track.url),
            audio_locale=attrs.get("audioLocale", track.audio_locale), storefront=storefront,
            raw={**track.raw, "catalog_id": item.get("id", catalog_id), "attributes": attrs},
        )


class PyncmClient:
    """Thin adapter. Keep pyncm calls here so provider changes do not affect the sync engine."""

    def __init__(self, session: dict[str, Any] | None = None) -> None:
        try:
            import pyncm  # type: ignore
            from pyncm.apis import playlist as playlist_api, search as search_api, track as track_api
        except ImportError as exc:  # pragma: no cover - exercised only without optional runtime deps
            raise RuntimeError("pyncm is required for Netease access") from exc
        self.pyncm = pyncm
        self.playlist_api = playlist_api
        self.search_api = search_api
        self.track_api = track_api
        if session:
            pyncm.load_cookie(session)

    def session(self) -> dict[str, Any]:
        return self.pyncm.dump_cookie()

    def login_qr(self) -> dict[str, Any]:
        """Run pyncm's interactive QR flow and return the persisted cookie/session."""
        from pyncm.apis import login as login_api  # type: ignore
        function = getattr(login_api, "login_qr", None)
        if function is None:
            raise RuntimeError("installed pyncm version does not expose login_qr")
        function()
        return self.session()

    def playlist_tracks(self, playlist: str) -> list[Candidate]:
        payload = self.playlist_api.get_playlist_detail(playlist_id(playlist))
        songs = payload.get("playlist", payload).get("tracks", [])
        return [self._track(song) for song in songs]

    def search(self, query: str) -> list[Candidate]:
        payload = self.search_api.cloudsearch(query, limit=30)
        songs = payload.get("result", {}).get("songs", [])
        return [self._track(song) for song in songs]

    def song(self, netease_id: str) -> Candidate:
        payload = self.track_api.get_track_detail([netease_id])
        songs = payload.get("songs", payload)
        return self._track(songs[0])

    def add(self, playlist: str, netease_id: str) -> None:
        self.playlist_api.add_track_to_playlist(playlist_id(playlist), netease_id)

    def remove(self, playlist: str, netease_id: str) -> None:
        self.playlist_api.delete_track_from_playlist(playlist_id(playlist), netease_id)

    @staticmethod
    def _track(song: dict[str, Any]) -> Candidate:
        artists = song.get("ar") or song.get("artists") or []
        album = song.get("al") or song.get("album") or {}
        return Candidate(id=str(song.get("id")), title=song.get("name", ""), artist=" / ".join(a.get("name", "") for a in artists), album=album.get("name", ""), duration_ms=song.get("dt") or song.get("duration"), url=f"https://music.163.com/#/song?id={song.get('id')}", raw=song)
