from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Track:
    id: str
    title: str
    artist: str
    album: str = ""
    duration_ms: int | None = None
    isrc: str | None = None
    url: str | None = None
    audio_locale: str | None = None
    storefront: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Candidate(Track):
    score: float = 0.0


@dataclass
class SyncPair:
    id: str
    name: str
    apple_playlist: str
    netease_playlist: str
    enabled: bool = True
    initial_policy: str = "preserve"
    removal_policy: str = "mirror"


@dataclass
class SyncResult:
    run_id: int
    status: str
    added: int = 0
    skipped: int = 0
    removed: int = 0
    failed: int = 0
    ambiguous: int = 0
    error: str | None = None
