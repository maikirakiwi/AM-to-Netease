from music_sync.database import SyncDatabase
from music_sync.models import Track


def test_mapping_and_run_are_persisted(tmp_path):
    db = SyncDatabase(tmp_path / "sync.sqlite3")
    run_id = db.start_run("pair")
    db.set_source("pair", Track("source", "Title", "Artist"))
    db.save_mapping("pair", "source", "dest", "synced")
    db.finish_run(run_id, "success", added=1)
    assert db.mapping("pair", "source")["dest_id"] == "dest"
    assert db.recent_runs()[0]["status"] == "success"
