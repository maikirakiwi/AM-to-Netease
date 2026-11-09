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


def title_credits(title: str) -> tuple[str, list[str]]:
    title = unicodedata.normalize("NFKC", title)
    featured = []

    def extract(match: re.Match) -> str:
        featured.append((match.group(1) or match.group(2)).strip())
        return ""

    # Extract credits only; recording labels such as TV size and live stay in the title.
    credit = r"(?:featuring\s+|feat\.\s*|feat\s+|with\s+)"
    title = re.sub(r"[\[(]\s*" + credit + r"([^\])]+)[\])]|\b(?:featuring\s+|feat\.\s*|feat\s+)([^()[\]]+)", extract, title, flags=re.I)
    return " ".join(title.split()), featured


def performers(artist: str) -> list[str]:
    artist = unicodedata.normalize("NFKC", artist)
    names = []
    for credit in re.split(r"\s*[/,&、;]\s*|\s+(?:featuring|feat\.?|with)\s+", artist, flags=re.I):
        cv = re.search(r"\bCV\s*[.:]\s*([^()]+)", credit, re.I)
        name = cv.group(1) if cv else credit
        for name in name.split("・") if cv else [name]:
            name = name.strip(" ()[]<>")
            if normalize(name) and normalize(name) not in {normalize(item) for item in names}:
                names.append(name)
    return names


def artist_similarity(source: Track, candidate: Track) -> float:
    def credits(track: Track) -> set[str]:
        _, featured = title_credits(track.title)
        return {normalize(name) for credit in [track.artist, *featured] for name in performers(credit)}

    left, right = credits(source), credits(candidate)
    if not left or not right:
        return 0.0
    return 2 * len(left & right) / (len(left) + len(right))


def album_title(album: str) -> str:
    return re.sub(r"\s*-\s*(?:EP|Single)\s*$", "", unicodedata.normalize("NFKC", album), flags=re.I)


def search_queries(source: Track) -> list[str]:
    title, _ = title_credits(source.title)
    names = performers(source.artist)
    queries = [f"{source.title} {source.artist}", f"{title} {names[0]}" if names else title, title]
    return list(dict.fromkeys(" ".join(query.split()) for query in queries if query.strip()))


def score(source: Track, candidate: Candidate) -> float:
    duration = 0.5
    if source.duration_ms and candidate.duration_ms:
        difference = abs(source.duration_ms - candidate.duration_ms)
        duration = max(0.0, 1 - difference / 15000)
    return (0.5 * _ratio(title_credits(source.title)[0], title_credits(candidate.title)[0])
            + 0.35 * artist_similarity(source, candidate)
            + 0.1 * _ratio(album_title(source.album), album_title(candidate.album)) + 0.05 * duration)


def choose(source: Track, candidates: list[Candidate]) -> tuple[Candidate | None, list[Candidate]]:
    ranked = sorted((Candidate(**{**candidate.__dict__, "score": score(source, candidate)}) for candidate in candidates), key=lambda item: item.score, reverse=True)
    title = normalize(title_credits(source.title)[0])
    album = normalize(album_title(source.album))
    exact = [candidate for candidate in ranked
             if title and album and normalize(title_credits(candidate.title)[0]) == title
             and normalize(album_title(candidate.album)) == album
             and not (source.duration_ms and candidate.duration_ms
                      and abs(source.duration_ms - candidate.duration_ms) > 15000)]
    if len(exact) == 1:
        return exact[0], ranked
    if not ranked or ranked[0].score < 0.80:
        return None, ranked
    if source.duration_ms and ranked[0].duration_ms and abs(source.duration_ms - ranked[0].duration_ms) > 15000:
        return None, ranked
    if len(ranked) > 1 and ranked[0].score - ranked[1].score < 0.05:
        return None, ranked
    return ranked[0], ranked
