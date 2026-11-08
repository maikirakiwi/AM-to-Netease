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


def test_progress_migrates_existing_database_and_survives_restart(tmp_path):
    import sqlite3
    path = tmp_path / 'old.sqlite3'
    conn = sqlite3.connect(path)
    conn.execute('CREATE TABLE sync_runs (id INTEGER PRIMARY KEY, pair_id TEXT, started_at TEXT, finished_at TEXT, status TEXT, current_source_id TEXT, added INTEGER DEFAULT 0, skipped INTEGER DEFAULT 0, removed INTEGER DEFAULT 0, failed INTEGER DEFAULT 0, ambiguous INTEGER DEFAULT 0, error TEXT)')
    conn.execute("INSERT INTO sync_runs (id, pair_id, status) VALUES (1, 'pair', 'running')")
    conn.commit()
    conn.close()
    db = SyncDatabase(path)
    assert db.run(1)['processed'] == 0
    assert db.run(1)['total'] is None
    db.update_run(1, total=3, processed=1, current_source_title='Song')
    db.interrupt_runs()
    reopened = SyncDatabase(path)
    row = reopened.run(1)
    assert row['total'] == 3 and row['processed'] == 1
    assert row['status'] == 'interrupted'
    assert 'restarted' in row['error']
