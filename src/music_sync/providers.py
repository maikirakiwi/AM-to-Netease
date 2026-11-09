from __future__ import annotations

import logging
import base64
import json
import time

import re
from typing import Any, Protocol
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

try:
    import httpx
except ImportError:  # pragma: no cover - used only in dependency-free smoke checks
    httpx = None  # type: ignore

from .models import Candidate, Track


logger = logging.getLogger(__name__)

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
    def audio_locale(self, track: Track) -> str | None: ...
    def metadata(self, track: Track, storefront: str) -> Track: ...
    def lyrics(self, track: Track) -> str: ...


class NeteaseProvider(Protocol):
    def playlist_tracks(self, playlist: str) -> list[Candidate]: ...
    def search(self, query: str) -> list[Candidate]: ...
    def lyrics(self, netease_id: str) -> str: ...
    def song(self, netease_id: str) -> Candidate: ...
    def add(self, playlist: str, netease_id: str) -> None: ...
    def remove(self, playlist: str, netease_id: str) -> None: ...


class AppleMusicClient:
    def __init__(self, developer_token: str, user_token: str, timeout: float = 30) -> None:
        self.developer_token = developer_token
        self.user_token = user_token
        self.timeout = timeout
        self.origin = None
        try:
            payload = developer_token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            # Apple's web-player token requires its origin even for server-side requests.
            if claims.get("iss") == "AMPWebPlay" and "apple.com" in claims.get("root_https_origin", []):
                self.origin = "https://music.apple.com"
        except (IndexError, ValueError, TypeError):
            pass

    def _get(self, path: str, storefront: str | None = None, params: dict[str, Any] | None = None, *, media: bool = False) -> dict[str, Any]:
        base = "https://amp-api.music.apple.com" if media else "https://api.music.apple.com"
        if path.startswith("/v1/"):
            url = f"{base}{path}"
        else:
            url = f"{base}/v1/catalog/{storefront or 'us'}{path}"
        headers = {"Authorization": f"Bearer {self.developer_token}", "Music-User-Token": self.user_token}
        if media or self.origin:
            headers["Media-User-Token"] = self.user_token
        if self.origin:
            headers.update({"Origin": self.origin, "Referer": self.origin + "/"})
        if httpx is not None:
            response = httpx.get(url, headers=headers, params=params, timeout=self.timeout)
            if response.status_code in (401, 403):
                raise RuntimeError(
                    f"Apple Music authentication failed (HTTP {response.status_code}). "
                    "Restart the service to refresh the Apple Music developer token, "
                    "then authorize Apple Music again using the same developer credentials. "
                    "The server has not accepted the supplied credentials."
                )
            response.raise_for_status()
            return response.json()
        from urllib.parse import urlencode
        from urllib.request import Request, urlopen
        if params:
            url += "?" + urlencode(params)
        with urlopen(Request(url, headers=headers), timeout=self.timeout) as response:  # nosec B310 - fixed HTTPS provider URL
            import json
            return json.load(response)

    def storefront(self) -> str:
        data = self._get("/v1/me/storefront").get("data", [])
        if not data or not data[0].get("id"):
            raise RuntimeError("Apple Music returned no storefront country code; authorize Apple Music again")
        return data[0]["id"]

    def tracks(self, playlist: str) -> list[Track]:
        playlist_value = playlist_id(playlist)
        storefront = self.storefront()
        if playlist_value.startswith("p."):
            path = f"/v1/me/library/playlists/{playlist_value}/tracks"
        else:
            path = f"/playlists/{playlist_value}/tracks"
        tracks: list[Track] = []
        items = []
        while path:
            payload = self._get(path, storefront=storefront)
            items.extend(payload["data"])
            path = payload.get("next")
        for item in items:
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

    def audio_locale(self, track: Track) -> str | None:
        if track.audio_locale:
            return track.audio_locale
        catalog_id = track.raw.get("catalog_id") or track.id
        try:
            payload = self._get(f"/songs/{catalog_id}", storefront=track.raw.get("storefront"),
                                params={"fields": "audioLocale"}, media=True)
            data = payload.get("data", [])
            return data[0].get("attributes", {}).get("audioLocale") if data else None
        except Exception:
            logger.warning("Apple audio locale lookup failed: source=%s", track.id, exc_info=True)
            return None

    def lyrics(self, track: Track) -> str:
        catalog_id = track.raw.get("catalog_id") or track.id
        storefronts = dict.fromkeys([track.storefront or "cn", track.raw.get("storefront") or track.storefront or "cn"])
        for storefront in storefronts:
            for endpoint in ("syllable-lyrics", "lyrics"):
                try:
                    payload = self._get(f"/songs/{catalog_id}/{endpoint}", storefront=storefront, media=True)
                    data = payload.get("data", [])
                    ttml = data[0].get("attributes", {}).get("ttml", "") if data else ""
                    if ttml:
                        root = ElementTree.fromstring(ttml)
                        lines = ["".join(p.itertext()).strip() for p in root.findall(".//{*}body//{*}p")]
                        text = "\n".join(line for line in lines if line)
                        if text:
                            return text
                except Exception:
                    logger.warning("Apple lyrics lookup failed: source=%s storefront=%s endpoint=%s", track.id, storefront, endpoint, exc_info=True)
        return ""

    def metadata(self, track: Track, storefront: str) -> Track:
        catalog_id = track.raw.get("catalog_id") or track.id
        try:
            payload = self._get(f"/songs/{catalog_id}", storefront=storefront)
            item = payload["data"][0]
        except Exception:
            logger.warning("Apple catalog lookup failed: source=%s storefront=%s; trying ISRC when available", track.id, storefront, exc_info=True)
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
            from pyncm.apis import playlist as playlist_api, cloudsearch as search_api, track as track_api
        except ImportError as exc:  # pragma: no cover - exercised only without optional runtime deps
            raise RuntimeError(f"Could not import sakarie9/pyncm: {exc}") from exc
        self.pyncm = pyncm
        self.playlist_api = playlist_api
        self.search_api = search_api
        self.track_api = track_api
        self._session = pyncm.Session()
        if session:
            self._session.load(session)

    def session(self) -> dict[str, Any]:
        return self._session.dump()

    def login_qr(self) -> dict[str, Any]:
        """Run pyncm's interactive QR flow and return the persisted cookie/session."""
        from pyncm.apis import login as login_api  # type: ignore
        import time
        import qrcode

        unikey = login_api.LoginQrcodeUnikey(session=self._session)["unikey"]
        url = login_api.GetLoginQRCodeUrl(unikey, session=self._session)
        qr = qrcode.QRCode()
        qr.add_data(url)
        qr.print_ascii(invert=True)
        print("Scan the QR code with the Netease Music app and confirm login.", flush=True)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            response = login_api.LoginQrcodeCheck(unikey, session=self._session)
            code = response.get("code")
            if code == 803:
                status = login_api.GetCurrentLoginStatus(session=self._session)
                self.pyncm.WriteLoginInfo(status, self._session)
                return self.session()
            if code not in (801, 802):
                raise RuntimeError(f"Netease QR login failed ({code}): {response.get('message', 'QR expired or rejected')}")
            time.sleep(2)
        raise TimeoutError("Netease QR login timed out; run setup again for a new QR code")

    def playlist_tracks(self, playlist: str) -> list[Candidate]:
        payload = self.playlist_api.GetPlaylistInfo(playlist_id(playlist), session=self._session)
        ids = [item["id"] for item in payload["playlist"]["trackIds"]]
        songs = []
        for offset in range(0, len(ids), 500):
            songs.extend(self.track_api.GetTrackDetail(ids[offset:offset + 500], session=self._session)["songs"])
        return [self._track(song) for song in songs]

    def search(self, query: str) -> list[Candidate]:
        for attempt in range(2):
            payload = self.search_api.GetSearchResult(query, limit=30, session=self._session)
            if not isinstance(payload, dict):
                raise RuntimeError("Netease search returned an invalid response")
            code = payload.get("code")
            # The fork's decrypted overseas responses omit code and set abroad=True.
            if code != 200 and not (code is None and payload.get("abroad") is True):
                if attempt == 0 and code in {405, 429, 500, 502, 503, 504}:
                    logger.warning("Netease search rejected; retrying: query=%r code=%s", query, code)
                    time.sleep(1)
                    continue
                raise RuntimeError(f"Netease search failed (code={code}); query={query!r}")
            result = payload.get("result")
            if not isinstance(result, dict):
                raise RuntimeError(f"Netease search returned no valid result; query={query!r}")
            songs = result.get("songs", [] if result.get("songCount") == 0 else None)
            if not isinstance(songs, list) or any(not isinstance(song, dict) or song.get("id") is None for song in songs):
                raise RuntimeError(f"Netease search returned invalid songs; query={query!r}")
            logger.info("Netease search: query=%r code=%s candidates=%s attempt=%s", query, code, len(songs), attempt + 1)
            if not songs and attempt == 0:
                logger.warning("Netease search returned zero candidates; retrying: query=%r", query)
                time.sleep(1)
                continue
            return [self._track(song) for song in songs]

    def song(self, netease_id: str) -> Candidate:
        payload = self.track_api.GetTrackDetail([netease_id], session=self._session)
        songs = payload.get("songs", payload)
        return self._track(songs[0])

    def lyrics(self, netease_id: str) -> str:
        payload = self.track_api.GetTrackLyrics(netease_id, session=self._session)
        if payload.get("code") != 200:
            raise RuntimeError(f"Netease lyrics lookup failed ({payload.get('code')})")
        return (payload.get("lrc") or {}).get("lyric", "")

    def add(self, playlist: str, netease_id: str) -> None:
        self._modify(playlist, netease_id, "add")

    def remove(self, playlist: str, netease_id: str) -> None:
        self._modify(playlist, netease_id, "del")

    def _modify(self, playlist: str, netease_id: str, operation: str) -> None:
        response = self.playlist_api.SetManipulatePlaylistTracks(
            [netease_id], playlist_id(playlist), op=operation, session=self._session
        )
        if response.get("code") != 200:
            raise RuntimeError(f"Netease playlist {operation} failed: {response.get('message', response.get('code'))}")

    @staticmethod
    def _track(song: dict[str, Any]) -> Candidate:
        artists = song.get("ar") or song.get("artists") or []
        album = song.get("al") or song.get("album") or {}
        return Candidate(id=str(song.get("id")), title=song.get("name", ""), artist=" / ".join(a.get("name", "") for a in artists), album=album.get("name", ""), duration_ms=song.get("dt") or song.get("duration"), url=f"https://music.163.com/#/song?id={song.get('id')}", raw=song)
