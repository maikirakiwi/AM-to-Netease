from music_sync.matching import choose, normalize
from music_sync.models import Candidate, Track


def test_normalize_handles_unicode_and_punctuation():
    assert normalize(" Ｊａｚｚ！ ") == "jazz"


def test_choose_accepts_unique_close_match():
    source = Track("a", "Song", "Artist", "Album", 180000)
    match, ranked = choose(source, [Candidate("n", "Song", "Artist", "Album", 180500)])
    assert match and match.id == "n"
    assert ranked[0].score >= 0.8


def test_choose_rejects_ambiguous_matches():
    source = Track("a", "Song", "Artist", "Album", 180000)
    candidates = [Candidate("1", "Song", "Artist", "Album", 180000), Candidate("2", "Song", "Artist", "Album", 180000)]
    match, _ = choose(source, candidates)
    assert match is None


def test_lyric_contains_ignores_timestamps_and_formatting():
    from music_sync.matching import contains_lyric_line
    assert contains_lyric_line('Ｈello, world!', '[00:01.20]Hello WORLD')
    assert contains_lyric_line('Hello world', '[00:01.20]Intro: Hello world — repeated')
    assert not contains_lyric_line('Hello world', '[00:01.20]Hello')
    assert not contains_lyric_line('', 'anything')
    assert not contains_lyric_line('hello world', 'hello\nworld')
