from __future__ import annotations

import argparse
import getpass
import secrets

from .settings import DEFAULT_SETTINGS, SettingsStore


def setup(settings: SettingsStore) -> None:
    data = settings.load()
    data["apple"]["developer_token"] = getpass.getpass("Apple developer token: ").strip()
    data["discord"]["webhook_url"] = input("Discord webhook URL (optional): ").strip()
    data["schedule"]["time"] = input("Daily sync time [03:00]: ").strip() or "03:00"
    data["schedule"]["timezone"] = input("Timezone [Asia/Shanghai]: ").strip() or "Asia/Shanghai"
    while input("Add a sync pair? [y/N]: ").lower().startswith("y"):
        data["sync_pairs"].append({
            "id": secrets.token_hex(8), "name": input("Pair name: ").strip(),
            "apple_playlist": input("Apple playlist URL or ID: ").strip(),
            "netease_playlist": input("Netease playlist URL or ID: ").strip(),
            "enabled": True,
            "initial_policy": input("Initial policy [preserve/mirror] (preserve): ").strip() or "preserve",
            "removal_policy": input("Removal policy [preserve/mirror] (mirror): ").strip() or "mirror",
        })
    settings.save(data)
    try:
        from .providers import PyncmClient
        client = PyncmClient()
        print("Starting Netease QR login...")
        data["netease"]["session"] = client.login_qr()
        settings.save(data)
    except Exception as exc:
        print(f"Netease QR login was not completed: {exc}")
    print(f"Saved {settings.path}. Open the dashboard to complete Apple Music MusicKit authorization.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Apple Music to Netease synchronizer")
    parser.add_argument("command", choices=["setup"], nargs="?", default="setup")
    parser.add_argument("--settings", default="settings.json")
    args = parser.parse_args()
    if args.command == "setup":
        setup(SettingsStore(args.settings))
