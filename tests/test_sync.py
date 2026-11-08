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
