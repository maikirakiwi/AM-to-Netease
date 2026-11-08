from __future__ import annotations

import html
import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse

from .database import SyncDatabase
from .models import SyncPair
from .notify import DiscordNotifier
from .providers import AppleMusicClient, PyncmClient, song_id
from .scheduler import SchedulerService
from .settings import SettingsError, SettingsStore
from .sync import SyncService


class Runtime:
    def __init__(self) -> None:
        self.settings = SettingsStore()
        self.db = SyncDatabase()
        self.sync: SyncService | None = None
        self.scheduler: SchedulerService | None = None
        self.rebuild()

    def rebuild(self) -> None:
        try:
            config = self.settings.load()
            apple = config.get("apple", {})
            netease = config.get("netease", {})
            if apple.get("developer_token") and apple.get("music_user_token"):
                apple_client = AppleMusicClient(apple["developer_token"], apple["music_user_token"])
                netease_client = PyncmClient(netease.get("session") or {})
                self.sync = SyncService(self.db, apple_client, netease_client, DiscordNotifier(config.get("discord", {}).get("webhook_url", "")))
                self.scheduler = SchedulerService(self.settings, self.sync)
        except (SettingsError, RuntimeError):
            self.sync = None
            self.scheduler = None


runtime = Runtime()


@asynccontextmanager
async def lifespan(_: FastAPI):
    if runtime.scheduler:
        runtime.scheduler.start()
    yield
    if runtime.scheduler:
        runtime.scheduler.stop()


app = FastAPI(title="Apple → Netease Sync", lifespan=lifespan)


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title><style>body{{font-family:system-ui;max-width:1100px;margin:2rem auto;padding:0 1rem;color:#222}}nav a{{margin-right:1rem}}table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #ddd;padding:.5rem;text-align:left}}input,select,button{{padding:.45rem;margin:.2rem 0}}.error{{color:#b00020}}.ok{{color:#176b2c}}</style></head><body><nav><a href='/'>Dashboard</a><a href='/settings'>Settings</a><a href='/setup/apple'>Apple authorization</a></nav>{body}</body></html>")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    try:
        pairs = runtime.settings.pairs()
        runs = runtime.db.recent_runs(20)
        rows = "".join(f"<tr><td>{html.escape(pair.name)}</td><td>{'enabled' if pair.enabled else 'disabled'}</td><td><a href='/pairs/{pair.id}/edit'>edit</a> <a href='/pairs/{pair.id}/mappings'>mappings</a> <form style='display:inline' method='post' action='/pairs/{pair.id}/sync'><button>Run now</button></form> <form style='display:inline' method='post' action='/pairs/{pair.id}/delete'><button>Delete</button></form></td></tr>" for pair in pairs)
        run_rows = "".join(f"<tr><td>{row['pair_id']}</td><td>{row['status']}</td><td>{row['added']}</td><td>{row['removed']}</td><td>{row['failed'] + row['ambiguous']}</td></tr>" for row in runs)
        body = f"<h1>Apple → Netease Sync</h1><p class='{'ok' if runtime.sync else 'error'}'>{'Provider clients ready' if runtime.sync else 'Setup required: configure Apple tokens and Netease session in Settings.'}</p><h2>Sync pairs</h2><form method='post' action='/pairs'><input name='name' placeholder='Pair name' required><input name='apple_playlist' placeholder='Apple playlist URL or ID' required><input name='netease_playlist' placeholder='Netease playlist URL or ID' required><select name='initial_policy'><option value='preserve'>preserve existing</option><option value='mirror'>mirror on first run</option></select><button>Add pair</button></form><table><tr><th>Name</th><th>Status</th><th>Actions</th></tr>{rows or '<tr><td colspan=3>No pairs configured</td></tr>'}</table><h2>Recent runs</h2><table><tr><th>Pair</th><th>Status</th><th>Added</th><th>Removed</th><th>Failures</th></tr>{run_rows or '<tr><td colspan=5>No runs yet</td></tr>'}</table>"
        return page("Dashboard", body)
    except SettingsError as exc:
        return page("Setup required", f"<h1>Setup required</h1><p class='error'>{html.escape(str(exc))}</p><p>Run <code>apple-netease-sync setup</code> and restart the service.</p>")


@app.post("/pairs")
def add_pair(name: str = Form(...), apple_playlist: str = Form(...), netease_playlist: str = Form(...), initial_policy: str = Form("preserve")) -> RedirectResponse:
    import secrets
    pair = SyncPair(secrets.token_hex(8), name, apple_playlist, netease_playlist, initial_policy=initial_policy)
    runtime.settings.upsert_pair(pair)
    if runtime.scheduler:
        runtime.scheduler.reload()
    return RedirectResponse("/", status_code=303)


@app.post("/pairs/{pair_id}/sync")
def run_pair(pair_id: str) -> RedirectResponse:
    if not runtime.sync:
        raise HTTPException(503, "provider setup is incomplete")
    pair = next((item for item in runtime.settings.pairs() if item.id == pair_id), None)
    if not pair:
        raise HTTPException(404, "pair not found")
    runtime.sync.sync(pair)
    return RedirectResponse("/", status_code=303)


@app.get("/pairs/{pair_id}/edit", response_class=HTMLResponse)
def edit_pair_page(pair_id: str) -> HTMLResponse:
    pair = next((item for item in runtime.settings.pairs() if item.id == pair_id), None)
    if not pair:
        raise HTTPException(404, "pair not found")
    body = f"<h1>Edit {html.escape(pair.name)}</h1><form method='post'><input name='name' value='{html.escape(pair.name)}' required><input name='apple_playlist' value='{html.escape(pair.apple_playlist)}' required><input name='netease_playlist' value='{html.escape(pair.netease_playlist)}' required><select name='initial_policy'><option {'selected' if pair.initial_policy == 'preserve' else ''} value='preserve'>preserve</option><option {'selected' if pair.initial_policy == 'mirror' else ''} value='mirror'>mirror</option></select><select name='removal_policy'><option {'selected' if pair.removal_policy == 'preserve' else ''} value='preserve'>preserve removals</option><option {'selected' if pair.removal_policy == 'mirror' else ''} value='mirror'>mirror removals</option></select><label><input type='checkbox' name='enabled' {'checked' if pair.enabled else ''}> enabled</label><button>Save</button></form>"
    return page("Edit pair", body)


@app.post("/pairs/{pair_id}/edit")
def edit_pair(pair_id: str, name: str = Form(...), apple_playlist: str = Form(...), netease_playlist: str = Form(...), initial_policy: str = Form("preserve"), removal_policy: str = Form("mirror"), enabled: str | None = Form(None)) -> RedirectResponse:
    pair = SyncPair(pair_id, name, apple_playlist, netease_playlist, enabled=enabled is not None, initial_policy=initial_policy, removal_policy=removal_policy)
    runtime.settings.upsert_pair(pair)
    if runtime.scheduler:
        runtime.scheduler.reload()
    return RedirectResponse("/", status_code=303)


@app.post("/pairs/{pair_id}/delete")
def delete_pair(pair_id: str) -> RedirectResponse:
    runtime.settings.delete_pair(pair_id)
    if runtime.scheduler:
        runtime.scheduler.reload()
    return RedirectResponse("/", status_code=303)


@app.get("/pairs/{pair_id}/mappings", response_class=HTMLResponse)
def mappings(pair_id: str) -> HTMLResponse:
    pair = next((item for item in runtime.settings.pairs() if item.id == pair_id), None)
    if not pair:
        raise HTTPException(404, "pair not found")
    rows = []
    for row in runtime.db.mappings(pair_id):
        rows.append(f"<tr><td>{html.escape(row['source_id'])}</td><td>{html.escape(row['dest_id'] or '')}</td><td>{row['state']}</td><td>{'manual' if row['manual'] else 'automatic'}</td><td><form method='post' action='/pairs/{pair_id}/mappings/{row['source_id']}/correct'><input name='url' placeholder='Netease song URL' required><button>Correct</button></form></td></tr>")
    body = f"<h1>{html.escape(pair.name)} mappings</h1><table><tr><th>Apple ID</th><th>Netease ID</th><th>State</th><th>Mode</th><th>Correction</th></tr>{''.join(rows) or '<tr><td colspan=5>No mappings</td></tr>'}</table>"
    return page("Mappings", body)


@app.post("/pairs/{pair_id}/mappings/{source_id}/correct")
def correct_mapping(pair_id: str, source_id: str, url: str = Form(...)) -> RedirectResponse:
    if not runtime.sync:
        raise HTTPException(503, "provider setup is incomplete")
    pair = next((item for item in runtime.settings.pairs() if item.id == pair_id), None)
    if not pair:
        raise HTTPException(404, "pair not found")
    try:
        runtime.sync.correct(pair, source_id, song_id(url))
    except Exception as exc:
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f"/pairs/{pair_id}/mappings", status_code=303)


@app.get("/setup/apple", response_class=HTMLResponse)
def apple_setup() -> HTMLResponse:
    token = runtime.settings.load().get("apple", {}).get("developer_token", "")
    token_json = json.dumps(token)
    body = f"""<h1>Apple Music authorization</h1>
    <p>Authorize this browser with MusicKit. The resulting user token is sent to this local service.</p>
    <script src='https://js-cdn.music.apple.com/musickit/v3/musickit.js'></script>
    <button id='authorize'>Authorize Apple Music</button><p id='status'></p>
    <script>
      document.addEventListener('musickitloaded', async () => {{
        const music = MusicKit.configure({{ developerToken: {token_json}, app: {{ name: 'Apple-Netease Sync', build: '0.1.0' }} }});
        document.getElementById('authorize').onclick = async () => {{
          document.getElementById('status').textContent = 'Waiting for Apple Music authorization…';
          try {{ const userToken = await music.authorize();
            await fetch('/setup/apple', {{ method: 'POST', headers: {{'Content-Type':'application/x-www-form-urlencoded'}}, body: 'user_token=' + encodeURIComponent(userToken) }});
            document.getElementById('status').textContent = 'Authorized. Return to the dashboard.';
          }} catch (error) {{ document.getElementById('status').textContent = String(error); }}
        }};
      }});
    </script>
    <p>Developer token configured: {'yes' if token else 'no'}</p>
    <details><summary>Manual token fallback</summary><form method='post' action='/setup/apple'><input type='text' name='user_token' placeholder='Music user token' size='80' required><button>Save user token</button></form></details>"""
    return page("Apple authorization", body)


@app.post("/setup/apple")
def save_apple_token(user_token: str = Form(...)) -> RedirectResponse:
    data = runtime.settings.load()
    data["apple"]["music_user_token"] = user_token.strip()
    runtime.settings.save(data)
    runtime.rebuild()
    return RedirectResponse("/", status_code=303)


@app.get("/settings", response_class=HTMLResponse)
def settings_page() -> HTMLResponse:
    data = runtime.settings.load()
    body = f"<h1>Settings</h1><form method='post'><label>Apple developer token<br><input name='developer_token' value='{html.escape(data['apple'].get('developer_token', ''))}' size='80'></label><br><label>Discord webhook<br><input name='webhook_url' value='{html.escape(data['discord'].get('webhook_url', ''))}' size='80'></label><br><label>Daily time<br><input name='schedule_time' value='{html.escape(data['schedule'].get('time', '03:00'))}'></label><br><label>Timezone<br><input name='timezone' value='{html.escape(data['schedule'].get('timezone', 'Asia/Shanghai'))}'></label><br><button>Save</button></form><form method='post' action='/setup/netease'><button>Start Netease QR login</button></form>"
    return page("Settings", body)


@app.post("/settings")
def save_settings(developer_token: str = Form(""), webhook_url: str = Form(""), schedule_time: str = Form("03:00"), timezone: str = Form("Asia/Shanghai")) -> RedirectResponse:
    data = runtime.settings.load()
    data["apple"]["developer_token"] = developer_token.strip()
    data["discord"]["webhook_url"] = webhook_url.strip()
    data["schedule"]["time"] = schedule_time.strip()
    data["schedule"]["timezone"] = timezone.strip()
    runtime.settings.save(data)
    runtime.rebuild()
    return RedirectResponse("/", status_code=303)


@app.post("/setup/netease")
def netease_setup() -> RedirectResponse:
    try:
        client = PyncmClient()
        data = runtime.settings.load()
        data["netease"]["session"] = client.login_qr()
        runtime.settings.save(data)
        runtime.rebuild()
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    return RedirectResponse("/", status_code=303)
