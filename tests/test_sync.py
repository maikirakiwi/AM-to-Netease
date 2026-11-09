from music_sync.database import SyncDatabase
from music_sync.models import Candidate, SyncPair, Track
from music_sync.sync import SyncService


class FakeApple:
    def __init__(self):
        self.source = Track("apple-1", "Song", "Artist", "Album", 180000, audio_locale="ja-JP")
        self.metadata_calls = 0

    def tracks(self, _playlist):
        return [self.source]

    def audio_locale(self, track):
        return track.audio_locale

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


def test_resolved_audio_locale_selects_japan_and_is_cached_by_mapping(tmp_path):
    apple, netease = FakeApple(), FakeNetease()
    apple.source.audio_locale = None
    locale_calls = []
    def resolve(track):
        locale_calls.append(track.id)
        return 'ja'
    apple.audio_locale = resolve
    service = SyncService(SyncDatabase(tmp_path / 'locale.sqlite3'), apple, netease)
    pair = SyncPair('pair', 'Pair', 'apple', 'netease')
    assert service.sync(pair).added == 1
    assert service.db.conn.execute('SELECT storefront FROM source_tracks').fetchone()[0] == 'jp'
    assert service.sync(pair).skipped == 1
    assert locale_calls == ['apple-1']


def wait_for_run(db, run_id):
    import time
    deadline = time.monotonic() + 4
    while db.run(run_id)['status'] in {'running', 'stopping', 'reverting'} and time.monotonic() < deadline:
        time.sleep(0.01)
    return db.run(run_id)


def test_stop_reverts_added_songs_new_mappings_and_restores_existing_mapping(tmp_path):
    import threading
    apple, netease = FakeApple(), FakeNetease()
    tracks = [Track('first', 'Song', 'Artist', 'Album', 180000, audio_locale='ja'), Track('second', 'Song', 'Artist', 'Album', 180000, audio_locale='ja')]
    apple.tracks = lambda _: tracks
    entered, release = threading.Event(), threading.Event()
    original = apple.metadata
    def metadata(track, storefront):
        if track.id == 'second':
            entered.set()
            assert release.wait(3)
        return original(track, storefront)
    apple.metadata = metadata
    netease.items = [Candidate('existing', 'Existing', 'Artist')]
    db = SyncDatabase(tmp_path / 'stop.sqlite3')
    db.save_mapping('pair', 'first', None, 'failed', error='previous error')
    previous = dict(db.mapping('pair', 'first'))
    service = SyncService(db, apple, netease)
    pair = SyncPair('pair', 'Pair', 'apple', 'netease', removal_policy='preserve')
    run = service.sync(pair, background=True)
    try:
        assert entered.wait(2)
        assert {t.id for t in netease.items} == {'existing', 'netease-1'}
        service.stop_and_revert(pair, run.run_id)
        assert db.run(run.run_id)['status'] == 'stopping'
    finally:
        release.set()
    assert wait_for_run(db, run.run_id)['status'] == 'cancelled'
    assert {t.id for t in netease.items} == {'existing'}
    assert dict(db.mapping('pair', 'first')) == previous
    assert db.mapping('pair', 'second') is None
    assert db.changes(run.run_id) == []


def test_stop_during_add_undoes_inflight_write_and_supports_retry(tmp_path):
    import threading
    apple, netease = FakeApple(), FakeNetease()
    entered, release = threading.Event(), threading.Event()
    original_add = netease.add
    def add(playlist, song):
        original_add(playlist, song)
        entered.set()
        assert release.wait(3)
    netease.add = add
    db = SyncDatabase(tmp_path / 'retry.sqlite3')
    service = SyncService(db, apple, netease)
    pair = SyncPair('pair', 'Pair', 'apple', 'netease')
    original_remove = netease.remove
    netease.remove = lambda *_: (_ for _ in ()).throw(RuntimeError('provider offline'))
    run = service.sync(pair, background=True)
    try:
        assert entered.wait(2)
        service.stop_and_revert(pair, run.run_id)
    finally:
        release.set()
    assert wait_for_run(db, run.run_id)['status'] == 'rollback_failed'
    assert db.changes(run.run_id)
    assert service.sync(pair).status == 'revert_required'
    netease.remove = original_remove
    service.stop_and_revert(pair, run.run_id)
    assert wait_for_run(db, run.run_id)['status'] == 'cancelled'
    assert netease.items == []
    assert db.mapping('pair', 'apple-1') is None


def test_stop_readds_songs_removed_during_mirror_cleanup(tmp_path):
    apple, netease = FakeApple(), FakeNetease()
    netease.items = [Candidate('existing', 'Existing', 'Artist')]
    db = SyncDatabase(tmp_path / 'mirror-stop.sqlite3')
    service = SyncService(db, apple, netease)
    pair = SyncPair('pair', 'Pair', 'apple', 'netease', initial_policy='mirror')
    original_remove = netease.remove
    def remove(playlist, song):
        original_remove(playlist, song)
        if song == 'existing':
            assert db.request_stop(db.latest_run('pair')['id'])
    netease.remove = remove
    run = service.sync(pair)
    assert run.status == 'cancelled'
    assert {t.id for t in netease.items} == {'existing'}
    assert db.mapping('pair', 'apple-1') is None


def test_flagged_incorrect_mapping_waits_for_manual_correction(tmp_path):
    apple, netease = FakeApple(), FakeNetease()
    service = SyncService(SyncDatabase(tmp_path / 'flag.sqlite3'), apple, netease)
    pair = SyncPair('pair', 'Pair', 'apple', 'netease')
    service.sync(pair)
    service.flag_incorrect(pair, 'apple-1')
    assert service.sync(pair).ambiguous == 1
    assert netease.search_calls == 1
    service.correct(pair, 'apple-1', 'correct')
    assert service.db.mapping('pair', 'apple-1')['manual']
    assert {t.id for t in netease.items} == {'correct'}


def test_removed_source_mapping_is_removed_without_deleting_shared_destination(tmp_path):
    apple, netease = FakeApple(), FakeNetease()
    service = SyncService(SyncDatabase(tmp_path / 'shared.sqlite3'), apple, netease)
    pair = SyncPair('pair', 'Pair', 'apple', 'netease')
    service.sync(pair)
    service.db.save_mapping('pair', 'removed-source', 'netease-1', 'synced')
    result = service.sync(pair)
    assert result.removed == 0
    assert service.db.mapping('pair', 'removed-source') is None
    assert service.db.mapping('pair', 'apple-1')['dest_id'] == 'netease-1'
    assert {t.id for t in netease.items} == {'netease-1'}


def test_multiple_lyric_matches_choose_highest_metadata_score_at_least_point_eight(tmp_path):
    apple, netease = FakeApple(), FakeNetease()
    apple.lyrics = lambda _: 'First line\nSecond line\nThird line'
    netease.lyrics = lambda song: '[00:01]prefix Second line suffix' if song != 'nonmatching' else 'Unrelated lyrics'
    service = SyncService(SyncDatabase(tmp_path / 'lyric-tie.sqlite3'), apple, netease)
    for first_score, second_score, expected in [
        (0.91, 0.89, 'first'),
        (0.89, 0.95, 'second'),
        (0.95, 0.91, 'first'),
        (0.89, 0.88, 'first'),
        (0.80, 0.79, 'first'),
        (0.79, 0.80, 'second'),
        (0.80, 0.80, 'first'),
        (0.799999, 0.79, None),
        (0.800001, 0.80, 'first'),
    ]:
        candidates = [Candidate('nonmatching', 'Other', 'Artist', score=0.99),
                      Candidate('first', 'Song', 'Artist', score=first_score),
                      Candidate('second', 'Song', 'Artist', score=second_score)]
        match, reason = service._match_lyrics(apple.source, candidates)
        assert (match.id if match else None) == expected
        if expected:
            assert reason == ''
        else:
            assert 'multiple candidates' in reason


def test_fallback_queries_merge_candidates_before_lyric_matching(tmp_path):
    apple, netease = FakeApple(), FakeNetease()
    apple.source = Track('apple-1', 'Song (feat. Guest)', 'Character(CV:Actor)', 'Album', 180000, audio_locale='ja')
    apple.lyrics = lambda _: 'First line'
    wrong = Candidate('wrong', 'Other', 'Other')
    correct = Candidate('correct', 'Song', 'Actor / Guest', 'Album', 180000)
    queries, lyric_calls = [], []
    def search(query):
        queries.append(query)
        if len(queries) < 3:
            return [wrong]
        # Equal metadata scores keep this ambiguous until lyrics are checked.
        return [wrong, correct, Candidate('alternate', 'Song', 'Guest / Actor', 'Album', 180000)]
    netease.search = search
    def lyrics(song):
        lyric_calls.append(song)
        return 'First line' if song == 'correct' else 'Other words'
    netease.lyrics = lyrics
    service = SyncService(SyncDatabase(tmp_path / 'fallback.sqlite3'), apple, netease)
    pair = SyncPair('pair', 'Pair', 'apple', 'netease', removal_policy='preserve')
    assert service.sync(pair).added == 1
    assert queries == ['Song (feat. Guest) Character(CV:Actor)', 'Song Actor', 'Song']
    assert len(lyric_calls) == len(set(lyric_calls)) == 3
    assert service.db.mapping('pair', 'apple-1')['dest_id'] == 'correct'
    assert service.sync(pair).skipped == 1
    assert len(queries) == 3


def test_fallback_stops_after_confident_second_query(tmp_path):
    apple, netease = FakeApple(), FakeNetease()
    apple.source.artist = 'Character(CV:Artist)'
    queries = []
    def search(query):
        queries.append(query)
        return [] if len(queries) == 1 else [Candidate('correct', 'Song', 'Artist', 'Album', 180000)]
    netease.search = search
    service = SyncService(SyncDatabase(tmp_path / 'fallback-stop.sqlite3'), apple, netease)
    assert service.sync(SyncPair('pair', 'Pair', 'apple', 'netease')).added == 1
    assert queries == ['Song Character(CV:Artist)', 'Song Artist']


def test_exact_title_album_match_ignores_artist_without_lyrics_or_fallback_search(tmp_path):
    apple, netease = FakeApple(), FakeNetease()
    queries = []
    def search(query):
        queries.append(query)
        return [Candidate('exact', 'Song', 'Different performer credit', 'Album', 180000)]
    netease.search = search
    service = SyncService(SyncDatabase(tmp_path / 'title-album.sqlite3'), apple, netease)
    result = service.sync(SyncPair('pair', 'Pair', 'apple', 'netease'))
    assert result.added == 1
    assert queries == ['Song Artist']
    mapping = service.db.mapping('pair', 'apple-1')
    assert mapping['dest_id'] == 'exact'
    assert mapping['match_method'] == 'metadata'


def test_exact_title_artist_skips_unmatched_lyrics_and_saves_mapping(tmp_path):
    apple, netease = FakeApple(), FakeNetease()
    def unexpected(*args):
        raise AssertionError('Exact title and artist should bypass lyrics')
    apple.lyrics = netease.lyrics = unexpected
    netease.search = lambda _: [
        Candidate('first', 'Song', 'Artist', 'Other album', 180000),
        Candidate('second', 'Song', 'Artist', 'Another album', 180000),
    ]
    service = SyncService(SyncDatabase(tmp_path / 'title-artist.sqlite3'), apple, netease)
    result = service.sync(SyncPair('pair', 'Pair', 'apple', 'netease'))
    assert result.added == 1
    mapping = service.db.mapping('pair', 'apple-1')
    assert mapping['dest_id'] == 'first'
    assert mapping['match_method'] == 'metadata'
