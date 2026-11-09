# Apple Music → Netease Music

Share your Apple Music playlists with friends on Netease · [简体中文](README.md)

- Sync your Apple Music playlists to your own Netease Music playlists, making it easier to share music with friends who use Netease.
- Sync automatically every day by default.
- The service looks up song titles and artists in their original language instead of relying on Apple Music’s romanized or translated names. Some songs still need help: find the correct song on the Netease website and paste its link into the dashboard to associate it manually.

Metadata lookup uses the song’s language to select a storefront where possible, with fallback regions when metadata is unavailable. The service supports multiple playlist pairs, a web dashboard, background progress, manual corrections, and optional Discord notifications.

## Get started

You need Docker, an Apple Music account, and a Netease playlist you can edit. Create the destination playlist in Netease first.

**Using Coolify:** follow the [Coolify deployment guide](COOLIFY.md) to deploy from Git and complete first-time setup.

**Running locally:** from the repository directory, run:

```bash
docker compose build
docker compose run --rm app apple-netease-sync setup
docker compose up -d
```

1. Follow the prompts to configure your schedule and playlist pairs, then scan the QR code to log in to Netease. The Apple Music developer token is fetched automatically.
2. Open [http://localhost:8008](http://localhost:8008) and authorize Apple Music from the settings page.
3. Manage playlist pairs under **Playlists** and select **Run now** to start syncing.

The default schedule is **03:00 Asia/Shanghai every day**. Change the time and timezone in the dashboard. To keep existing destination songs and only add new ones, set both the initial policy and removal policy to **preserve**.

## Fix a song match

Open **Needs attention**, select **Review**, paste the correct Netease song URL, and select **Apply correction**. For an incorrect song already marked as synced, select **Flag as incorrect** in its details first, then apply the correction. Future syncs preserve your manual choice.

Syncs run in the background with progress tracking. Use **Stop & revert changes** to stop a running sync and undo that run’s changes.

## Data and further reading

Keep the Docker `app-data` volume: it stores account authorization, settings, and song mappings. The dashboard has no built-in login; use a trusted network or external authentication.

- [Matching algorithms, sync policies, and technical details (Chinese)](DETAILS.md)
- [Coolify deployment and first-time setup](COOLIFY.md)
