from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

from .models import Candidate, Track


def normalize(value: str) -> str:
    value = unicodedata.normalize("NFKC", value or "").casefold()
    return re.sub(r"[^\w]+", "", value, flags=re.UNICODE)


def contains_lyric_line(first_line: str, lyrics: str) -> bool:
    expected = normalize(first_line)
    return bool(expected) and any(
        expected in normalize(re.sub(r"\[[^\]]*\]", "", line))
        for line in lyrics.splitlines()
    )


def _ratio(left: str, right: str) -> float:
    return SequenceMatcher(None, normalize(left), normalize(right)).ratio()


def score(source: Track, candidate: Candidate) -> float:
    duration = 0.5
    if source.duration_ms and candidate.duration_ms:
        difference = abs(source.duration_ms - candidate.duration_ms)
        duration = max(0.0, 1 - difference / 15000)
    return 0.5 * _ratio(source.title, candidate.title) + 0.35 * _ratio(source.artist, candidate.artist) + 0.1 * _ratio(source.album, candidate.album) + 0.05 * duration


def choose(source: Track, candidates: list[Candidate]) -> tuple[Candidate | None, list[Candidate]]:
    ranked = sorted((Candidate(**{**candidate.__dict__, "score": score(source, candidate)}) for candidate in candidates), key=lambda item: item.score, reverse=True)
    if not ranked or ranked[0].score < 0.90:
        return None, ranked
    if source.duration_ms and ranked[0].duration_ms and abs(source.duration_ms - ranked[0].duration_ms) > 15000:
        return None, ranked
    if len(ranked) > 1 and ranked[0].score - ranked[1].score < 0.05:
        return None, ranked
    return ranked[0], ranked
