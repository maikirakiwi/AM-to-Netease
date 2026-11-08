# Apple Music → Netease Music sync

This is a local-network Docker service that copies tracks from private Apple Music playlists into existing Netease playlists. It keeps source-to-destination mappings in SQLite, supports multiple pairs, and runs a daily sync from the dashboard scheduler.

## First run

```bash
docker compose run --rm app apple-netease-sync setup
docker compose up -d
open http://127.0.0.1:8000
```

The setup flow stores credentials and sessions in `data/settings.json`. Keep the dashboard on a trusted private network; it intentionally has no built-in authentication.

Apple Music requires an Apple developer token and a MusicKit user token. Netease access uses the pinned `pyncm` adapter and its persisted session. The dashboard can reauthorize Apple Music and update settings after setup.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
uvicorn music_sync.app:app --reload
```
