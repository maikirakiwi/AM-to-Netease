# Coolify Deployment

This project can be deployed to Coolify as a Docker Compose application from the Git repository.

## Create the application

1. In Coolify, choose **Applications → New → Docker Compose**.
2. Select **Git Repository** as the source.
3. Point the application at this repository.
4. Use `compose.yaml` as the Compose file.
5. Configure the `app` service with container port `8008`.
6. Attach the domain you want to use for the dashboard.
7. Deploy the application.

The Compose file exposes port `8008` internally for Coolify’s proxy. It stores settings, provider sessions, and SQLite data in the named `app-data` volume. Keep that volume when redeploying or updating the application.

## First-time setup

After the first deployment, open the terminal for the running `app` service in Coolify and run:

```bash
apple-netease-sync setup
```

Complete the prompts for:

- Apple Music developer token.
- Discord webhook, if needed.
- Daily sync time and timezone.
- Apple Music and Netease playlist pairs.

The command then starts the Netease QR login flow. Scan the QR code and wait for the login session to be saved to the persistent volume.

Restart the `app` service from Coolify after setup. Open the configured domain and choose **Apple authorization** to complete MusicKit user-token authorization in a browser.

The dashboard can later reauthorize Apple Music, start another Netease QR login, edit settings, and manage playlist pairs.

## Network and storage

The dashboard has no built-in login. Restrict its Coolify domain to a trusted network or place it behind an access-control layer before exposing it publicly.

Do not delete or replace the `app-data` volume. It contains:

- `settings.json`, including provider sessions.
- `sync.sqlite3`, including mappings and run history.

The service health endpoint is available at `/healthz`.
