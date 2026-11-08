from music_sync.database import SyncDatabase
from music_sync.models import Candidate, SyncPair, Track
from music_sync.sync import SyncService


class FakeApple:
    def __init__(self):
        self.source = Track("apple-1", "Song", "Artist", "Album", 180000, audio_locale="ja-JP")
        self.metadata_calls = 0

    def tracks(self, _playlist):
        return [self.source]

    def metadata(self, track, storefront):
        self.metadata_calls += 1
        return Track(track.id, track.title, track.artist, track.album, track.duration_ms, storefront=storefront)


class FakeNetease:
    def __init__(self):
        self.items = []
        self.search_calls = 0
        self.add_calls = 0

    def playlist_tracks(self, _playlist):
        return list(self.items)

    def search(self, _query):
        self.search_calls += 1
        return [Candidate("netease-1", "Song", "Artist", "Album", 180000)]

    def song(self, netease_id):
        return Candidate(netease_id, "Song", "Artist", "Album", 180000)

    def add(self, _playlist, netease_id):
        self.add_calls += 1
        self.items.append(self.song(netease_id))

    def remove(self, _playlist, netease_id):
        self.items = [item for item in self.items if item.id != netease_id]


def test_sync_is_idempotent_and_uses_japanese_storefront(tmp_path):
    apple, netease = FakeApple(), FakeNetease()
    service = SyncService(SyncDatabase(tmp_path / "sync.sqlite3"), apple, netease)
    pair = SyncPair("pair", "Pair", "apple", "netease")

    first = service.sync(pair)
    second = service.sync(pair)

    assert first.added == 1
    assert second.skipped == 1
    assert netease.add_calls == 1
    assert netease.search_calls == 1
    assert apple.metadata_calls == 1
    assert service.db.mapping("pair", "apple-1")["dest_id"] == "netease-1"


def test_initial_mirror_removes_unmapped_destination(tmp_path):
    apple, netease = FakeApple(), FakeNetease()
    netease.items = [Candidate("old", "Old", "Artist", "Album", 180000)]
    service = SyncService(SyncDatabase(tmp_path / "sync.sqlite3"), apple, netease)
    result = service.sync(SyncPair("pair", "Pair", "apple", "netease", initial_policy="mirror"))
    assert result.removed == 1
    assert [item.id for item in netease.items] == ["netease-1"]


def test_sync_failure_is_logged(tmp_path, caplog):
    apple, netease = FakeApple(), FakeNetease()
    def fail(_playlist):
        raise RuntimeError("provider unavailable")
    apple.tracks = fail
    service = SyncService(SyncDatabase(tmp_path / "sync.sqlite3"), apple, netease)
    result = service.sync(SyncPair("pair", "Pair", "apple", "netease"))
    assert result.status == "failed"
    assert "Playlist sync failed: run=1 pair=pair" in caplog.text
    assert "provider unavailable" in caplog.text
    assert caplog.records[-1].exc_info


def test_lyric_fallback_checks_top_ten_and_caches_mapping(tmp_path):
    apple, netease = FakeApple(), FakeNetease()
    apple.lyrics = lambda track: '\n最初の歌詞\nsecond line'
    candidates = [Candidate(str(i), 'Different', 'Name') for i in range(11)]
    netease.search = lambda query: candidates
    checked = []
    def lyrics(song):
        checked.append(song)
        return '[00:10.00]最初 の 歌詞！' if song == '6' else '[00:00.00]other words'
    netease.lyrics = lyrics
    service = SyncService(SyncDatabase(tmp_path / 'test.sqlite3'), apple, netease)
    pair = SyncPair('pair', 'Pair', 'apple', 'netease', removal_policy='preserve')
    assert service.sync(pair).added == 1
    assert checked == [str(i) for i in range(10)]
    assert service.db.mapping('pair', 'apple-1')['dest_id'] == '6'
    assert service.sync(pair).skipped == 1
    assert len(checked) == 10


def test_lyric_fallback_unresolved_cases_require_manual_correction(tmp_path):
    for case in ('missing', 'none', 'multiple', 'error'):
        apple, netease = FakeApple(), FakeNetease()
        apple.lyrics = lambda track: '' if case == 'missing' else 'First line'
        netease.search = lambda query: [Candidate('1', 'Other', 'Other'), Candidate('2', 'Other', 'Other')]
        def lyrics(song):
            if case == 'error':
                raise RuntimeError('unavailable')
            return 'First line' if case == 'multiple' else 'unrelated'
        netease.lyrics = lyrics
        netease.items = [Candidate('existing', 'Existing', 'Artist')]
        service = SyncService(SyncDatabase(tmp_path / f'{case}.sqlite3'), apple, netease)
        result = service.sync(SyncPair('pair', 'Pair', 'apple', 'netease', initial_policy='mirror'))
        assert result.ambiguous == 1
        assert result.added == result.removed == 0
        assert 'manual correction required' in service.db.mapping('pair', 'apple-1')['last_error']


def test_lyric_candidate_error_does_not_stop_remaining_candidates(tmp_path):
    apple, netease = FakeApple(), FakeNetease()
    apple.lyrics = lambda track: 'First line'
    netease.search = lambda query: [Candidate('1', 'Other', 'Other'), Candidate('2', 'Other', 'Other')]
    def lyrics(song):
        if song == '1':
            raise RuntimeError('unavailable')
        return '[00:03]First line'
    netease.lyrics = lyrics
    service = SyncService(SyncDatabase(tmp_path / 'test.sqlite3'), apple, netease)
    assert service.sync(SyncPair('pair', 'Pair', 'apple', 'netease')).added == 1
    assert service.db.mapping('pair', 'apple-1')['dest_id'] == '2'


def test_lyric_fallback_uses_only_first_three_complete_lines(tmp_path):
    apple, netease = FakeApple(), FakeNetease()
    apple.lyrics = lambda track: '\nFirst complete line\n\nSecond complete line\nThird complete line\nFourth complete line'
    calls = []
    candidate = Candidate('1', 'Other', 'Other')
    service = SyncService(SyncDatabase(tmp_path / 'lyrics.sqlite3'), apple, netease)
    cases = [
        ('[00:01]prefix First complete line suffix', True),
        ('[00:02]prefix Second complete line suffix', True),
        ('[00:03]prefix Third complete line suffix', True),
        ('[00:04]prefix Fourth complete line suffix', False),
        ('[00:01]First complete', False),
        ('First complete\nline', False),
    ]
    for text, expected in cases:
        def lyrics(song):
            calls.append(song)
            return text
        netease.lyrics = lyrics
        match, reason = service._match_lyrics(apple.source, [candidate])
        assert (match is candidate) == expected
        if not expected:
            assert 'manual correction required' in reason
    assert calls == ['1'] * len(cases)
