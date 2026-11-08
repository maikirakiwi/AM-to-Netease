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

When metadata cannot identify a confident match, the service checks the top 10 Netease candidates against the first three nonempty Apple Music lyric lines (or all available lines if fewer than three). A candidate qualifies when any individual Netease lyric line contains at least one complete selected Apple line, allowing additional characters before or after it and ignoring timing tags, punctuation, spacing, and case. A single qualifying candidate is accepted. Missing lyrics, no lyric match, or multiple matching candidates leave the song for manual correction in the dashboard. Successful mappings skip lyric lookups on later runs.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
uvicorn music_sync.app:app --reload
```
