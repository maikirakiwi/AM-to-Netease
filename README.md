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

Search starts with the original Apple title and artist. If metadata matching remains unresolved, it tries the cleaned title with the first extracted performer, then the cleaned title alone. Duplicate queries are skipped, results are merged by Netease song ID, and searching stops at the first confident metadata match. Artist scoring compares normalized performer sets, extracts voice actors from `CV:`/`CV.` credits, and ignores performer order. Featured credits are compared as performers even when one provider puts them in the title. Album comparison ignores trailing `- EP` and `- Single`; recording labels such as TV size, live, game version, and karaoke remain in the title. These changes apply to new or unresolved songs; successful saved mappings keep their existing IDs.

Netease search logs include the query, response code, candidate count, and attempt number. Empty searches and transient rejection codes get one retry. Invalid or rejected responses are recorded as provider failures instead of being reported as “no candidates.”

When metadata cannot identify a confident match after those searches, the service checks the top 10 combined Netease candidates against the first three nonempty Apple Music lyric lines (or all available lines if fewer than three). A candidate qualifies when any individual Netease lyric line contains at least one complete selected Apple line, allowing additional characters before or after it and ignoring timing tags, punctuation, spacing, and case. A single qualifying candidate is accepted. If multiple candidates match the lyrics, the service selects the one with the highest metadata score as long as that score is at least 80. Equal highest scores use the first candidate in the ranked results. Missing lyrics, no lyric match, or multiple lyric matches whose highest metadata score is below 0.80 leave the song for manual correction in the dashboard. Successful mappings skip lyric lookups on later runs.

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest
uvicorn music_sync.app:app --reload
```

## Dashboard and song review

The dashboard provides an overview of saved song mappings, recent run outcomes, playlist connections, and an attention queue. The song library supports title/artist/ID search, status filters, and pagination. Expand a song's details to inspect its Apple metadata, selected storefront, Netease destination, match method, candidate scores, and failure reason.

A mapping marked **Synced** means the service saved a successful match; it is not a separate human verification. If a saved mapping is wrong, use **Flag as incorrect** to hold it for manual review, then paste the correct Netease song URL under **Apply correction**. Flagged mappings stay out of automatic matching until corrected.

Manual dashboard runs execute in the background. Their progress page shows the current song, processed count, items left, and outcomes, and continues updating after a page reload. If a playlist response omits `audioLocale`, the service explicitly requests it from Apple's media API before choosing the metadata storefront.

## Stop and revert a running sync

Use **Stop & revert changes** on a run's progress page. The worker finishes its in-flight provider request, stops further processing, then uses its persistent change journal to:

- Remove destination songs added by that run, preserving songs that were already present.
- Restore destination songs removed during that run's mirror cleanup.
- Delete newly created mappings and restore mappings that existed before the run.

Restoring removed songs may change their playlist order. A provider error leaves the run in **Revert incomplete**, with the failed operation recorded and a **Retry revert** action. A restart marks an active run **Interrupted**, and its journal remains available for a retry. A run with an unfinished revert blocks another sync for that pair until the revert completes. Keep the service running until the progress page confirms the revert finished.

The journal covers runs created by this version; it cannot undo changes from earlier versions. Already-sent Discord notifications cannot be recalled. Provider requests cannot be forcibly interrupted, so stopping may take as long as the current request.
