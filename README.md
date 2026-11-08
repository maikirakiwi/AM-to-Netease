# Apple Music → Netease Music sync

This is a local-network Docker service that copies tracks from private Apple Music playlists into existing Netease playlists. It keeps source-to-destination mappings in SQLite, supports multiple pairs, and runs a daily sync from the dashboard scheduler.

## Deployment

See the [Coolify deployment guide](COOLIFY.md) for Git Repository deployment, persistent storage, and first-time setup.

## Local first run

```bash
docker compose run --rm app apple-netease-sync setup
docker compose up -d
```

The local Compose service is reachable through the container network. For a host browser, add a local port mapping such as `127.0.0.1:8008:8008` to the `app` service or use a small Compose override. The setup flow stores credentials and sessions in the persistent `app-data` volume.

Apple Music requires an Apple developer token and a MusicKit user token. Netease access uses the [`sakarie9/pyncm`](https://github.com/sakarie9/pyncm) adapter and its persisted session. The dashboard can reauthorize Apple Music and update settings after setup.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
uvicorn music_sync.app:app --reload
```
