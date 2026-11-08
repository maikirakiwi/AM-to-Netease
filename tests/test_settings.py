import os

from music_sync.settings import SettingsStore


def test_settings_round_trip_is_private(tmp_path):
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    data = store.load()
    store.save(data)
    assert os.stat(path).st_mode & 0o077 == 0
    assert store.load()["version"] == 1
