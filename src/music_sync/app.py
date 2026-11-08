from __future__ import annotations

import logging

import json
from contextlib import asynccontextmanager
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse, JSONResponse

from .database import SyncDatabase
from .models import SyncPair
from .notify import DiscordNotifier
from .providers import AppleMusicClient, PyncmClient, song_id
from .scheduler import SchedulerService
from .settings import SettingsError, SettingsStore
from .sync import SyncService


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

class Runtime:
    def __init__(self) -> None:
        self.settings = SettingsStore()
        self.db = SyncDatabase()
        self.db.interrupt_runs()
        self.sync: SyncService | None = None
        self.scheduler: SchedulerService | None = None
        self.started = False
        self.rebuild()

    def rebuild(self) -> None:
        previous_sync = self.sync
        if self.scheduler:
            self.scheduler.stop()
        self.sync = None
        self.scheduler = None
        try:
            config = self.settings.load()
            apple = config.get("apple", {})
            netease = config.get("netease", {})
            if apple.get("developer_token") and apple.get("music_user_token"):
                apple_client = AppleMusicClient(apple["developer_token"], apple["music_user_token"])
                netease_client = PyncmClient(netease.get("session") or {})
                self.sync = SyncService(self.db, apple_client, netease_client, DiscordNotifier(config.get("discord", {}).get("webhook_url", "")))
                if previous_sync:
                    self.sync._locks = previous_sync._locks
                self.scheduler = SchedulerService(self.settings, self.sync)
                if self.started:
                    self.scheduler.start()
        except (SettingsError, RuntimeError):
            logger.exception("Provider initialization failed")
            self.sync = None
            self.scheduler = None


runtime = Runtime()


@asynccontextmanager
async def lifespan(_: FastAPI):
    runtime.started = True
    if runtime.scheduler:
        runtime.scheduler.start()
    yield
    if runtime.scheduler:
        runtime.scheduler.stop()
    runtime.started = False


app = FastAPI(title="Apple → Netease Sync", lifespan=lifespan)


ACTIVE_STATES = {"running", "stopping", "reverting"}
TEMPLATES = Environment(loader=FileSystemLoader(Path(__file__).with_name("templates")), autoescape=select_autoescape(["html"]))


def icon(name: str) -> Markup:
    return Markup('<svg class="icon" aria-hidden="true"><use href="#i-{}"></use></svg>').format(name)


def format_datetime(value: str | None) -> str:
    if not value:
        return "Not available"
    try:
        return datetime.fromisoformat(value).astimezone(ZoneInfo("Asia/Shanghai")).strftime("%b %d, %H:%M")
    except ValueError:
        return value


TEMPLATES.globals["icon"] = icon
TEMPLATES.filters["datetime"] = format_datetime
TEMPLATES.filters["number"] = lambda value: f"{value:,}"


def configured_pairs() -> list[SyncPair]:
    return runtime.settings.pairs()


def get_pair(pair_id: str) -> SyncPair:
    pair = next((item for item in configured_pairs() if item.id == pair_id), None)
    if not pair:
        raise HTTPException(404, "Playlist connection not found")
    return pair


def all_songs(pairs: list[SyncPair]) -> list[dict]:
    return [dict(song, pair_name=pair.name) for pair in pairs for song in runtime.db.mapping_details(pair.id)]


def statistics(songs: list[dict]) -> dict:
    counts = {state: sum(song["state"] == state for song in songs) for state in ("synced", "ambiguous", "failed", "incorrect")}
    counts.update(total=len(songs), manual=sum(song["state"] == "synced" and song["manual"] for song in songs))
    counts["attention"] = counts["ambiguous"] + counts["failed"] + counts["incorrect"]
    counts["rate"] = round(counts["synced"] / len(songs) * 100) if songs else 0
    return counts


def render(template: str, title: str, active: str = "overview", **context) -> HTMLResponse:
    try:
        attention_count = statistics(all_songs(configured_pairs()))["attention"]
    except SettingsError:
        attention_count = 0
    return HTMLResponse(TEMPLATES.get_template(template).render(title=title, active=active, ready=bool(runtime.sync), attention_count=attention_count, **context))


def runs_with_names(pairs: list[SyncPair]) -> list[dict]:
    names = {pair.id: pair.name for pair in pairs}
    return [dict(row, pair_name=names.get(row["pair_id"], row["pair_id"])) for row in runtime.db.recent_runs(50)]


def pair_cards(pairs: list[SyncPair], songs: list[dict]) -> list[dict]:
    cards = []
    for pair in pairs:
        own = [song for song in songs if song["pair_id"] == pair.id]
        cards.append(dict(pair=pair, synced=sum(song["state"] == "synced" for song in own),
                          attention=sum(song["state"] != "synced" for song in own), run=runtime.db.latest_run(pair.id)))
    return cards


def require_idle(pair_id: str) -> None:
    latest = runtime.db.latest_run(pair_id)
    if latest and (latest["status"] in ACTIVE_STATES or (latest["status"] in {"rollback_failed", "interrupted"} and runtime.db.changes(latest["id"]))):
        raise HTTPException(409, "Stop the active sync and wait for its revert before changing this connection")


@app.exception_handler(HTTPException)
async def show_http_error(request: Request, exc: HTTPException):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": exc.detail}, status_code=exc.status_code)
    response = render("error.html", "Action needs attention", message=str(exc.detail))
    response.status_code = exc.status_code
    return response


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/static/{filename}")
def static_asset(filename: str) -> FileResponse:
    path = Path(__file__).with_name(filename)
    if path.suffix not in {".js", ".css"} or not path.is_file():
        raise HTTPException(404, "Asset not found")
    return FileResponse(path, media_type="text/css" if path.suffix == ".css" else "text/javascript")


@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    try:
        pairs = configured_pairs()
        songs = all_songs(pairs)
        stats = statistics(songs)
        runs = runs_with_names(pairs)
        chart = list(reversed(runs[:14]))
        return render("overview.html", "Overview", config=runtime.settings.load(), pairs=pairs,
                      stats=stats, attention=[s for s in songs if s["state"] != "synced"], runs=runs,
                      chart=chart, chart_max=max([r["added"] + r["skipped"] + r["failed"] + r["ambiguous"] for r in chart] or [1]) or 1,
                      pair_cards=pair_cards(pairs, songs))
    except SettingsError as exc:
        logger.exception("Dashboard settings could not be loaded")
        return render("error.html", "Setup required", message=str(exc))


@app.get("/playlists")
def playlists() -> HTMLResponse:
    pairs = configured_pairs()
    return render("playlists.html", "Playlists", "pairs", pairs=pairs, pair_cards=pair_cards(pairs, all_songs(pairs)))


@app.post("/pairs")
def add_pair(name: str = Form(...), apple_playlist: str = Form(...), netease_playlist: str = Form(...), initial_policy: str = Form("preserve")) -> RedirectResponse:
    import secrets
    try:
        runtime.settings.upsert_pair(SyncPair(secrets.token_hex(8), name.strip(), apple_playlist.strip(), netease_playlist.strip(), initial_policy=initial_policy))
    except SettingsError as exc:
        raise HTTPException(400, str(exc)) from exc
    if runtime.scheduler:
        runtime.scheduler.reload()
    return RedirectResponse("/playlists", status_code=303)


@app.post("/pairs/{pair_id}/sync")
def run_pair(pair_id: str) -> RedirectResponse:
    if not runtime.sync:
        raise HTTPException(503, "Provider setup is incomplete")
    pair = get_pair(pair_id)
    result = runtime.sync.sync(pair, background=True)
    run_id = result.run_id
    if result.status == "already_running":
        latest = runtime.db.latest_run(pair_id)
        if not latest:
            raise HTTPException(409, "Sync already running")
        run_id = latest["id"]
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.get("/api/runs/{run_id}")
def run_status(run_id: int) -> dict:
    row = runtime.db.run(run_id)
    if not row:
        raise HTTPException(404, "Run not found")
    data = dict(row)
    data["remaining"] = max(0, data["total"] - data["processed"]) if data["total"] is not None else None
    return data


@app.get("/runs/{run_id}")
def progress_page(run_id: int) -> HTMLResponse:
    row = runtime.db.run(run_id)
    if not row:
        raise HTTPException(404, "Run not found")
    pairs = configured_pairs()
    pair_name = next((pair.name for pair in pairs if pair.id == row["pair_id"]), row["pair_id"])
    with runtime.db.lock:
        changes = runtime.db.conn.execute("SELECT * FROM run_changes WHERE run_id = ? ORDER BY id DESC", (run_id,)).fetchall()
    return render("progress.html", "Sync progress", "activity", run=dict(row), pair_name=pair_name, changes=changes)


@app.post("/runs/{run_id}/stop")
def stop_run(run_id: int) -> RedirectResponse:
    if not runtime.sync:
        raise HTTPException(503, "Provider setup is incomplete")
    row = runtime.db.run(run_id)
    if not row:
        raise HTTPException(404, "Run not found")
    try:
        runtime.sync.stop_and_revert(get_pair(row["pair_id"]), run_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(f"/runs/{run_id}", status_code=303)


@app.get("/activity")
def activity() -> HTMLResponse:
    return render("activity.html", "Sync activity", "activity", runs=runs_with_names(configured_pairs()))


@app.get("/pairs/{pair_id}/edit")
def edit_pair_page(pair_id: str) -> HTMLResponse:
    pair = get_pair(pair_id)
    pairs = configured_pairs()
    return render("playlists.html", "Edit playlist", "pairs", pairs=pairs, editing=pair, pair_cards=pair_cards(pairs, all_songs(pairs)))


@app.post("/pairs/{pair_id}/edit")
def edit_pair(pair_id: str, name: str = Form(...), apple_playlist: str = Form(...), netease_playlist: str = Form(...), initial_policy: str = Form("preserve"), removal_policy: str = Form("mirror"), enabled: str | None = Form(None)) -> RedirectResponse:
    get_pair(pair_id)
    require_idle(pair_id)
    try:
        runtime.settings.upsert_pair(SyncPair(pair_id, name.strip(), apple_playlist.strip(), netease_playlist.strip(), enabled=enabled is not None, initial_policy=initial_policy, removal_policy=removal_policy))
    except SettingsError as exc:
        raise HTTPException(400, str(exc)) from exc
    if runtime.scheduler:
        runtime.scheduler.reload()
    return RedirectResponse("/playlists", status_code=303)


@app.post("/pairs/{pair_id}/delete")
def delete_pair(pair_id: str) -> RedirectResponse:
    get_pair(pair_id)
    require_idle(pair_id)
    runtime.settings.delete_pair(pair_id)
    if runtime.scheduler:
        runtime.scheduler.reload()
    return RedirectResponse("/playlists", status_code=303)


def song_page(pair: SyncPair | None, state: str, query: str, page_number: int, attention: bool = False) -> HTMLResponse:
    if state not in {"all", "synced", "ambiguous", "failed", "incorrect"}:
        raise HTTPException(400, "Unknown song state")
    pairs = [pair] if pair else configured_pairs()
    songs = all_songs(pairs)
    if attention:
        songs = [song for song in songs if song["state"] != "synced"]
    counts = {key: sum(song["state"] == key for song in songs) for key in ("synced", "ambiguous", "failed", "incorrect")}
    counts["all"] = len(songs)
    selected = [song for song in songs if (state == "all" or song["state"] == state) and
                (not query or query.casefold() in " ".join(str(song.get(key) or "") for key in ("title", "artist", "source_id", "dest_id", "pair_name")).casefold())]
    total = len(selected)
    total_pages = max(1, (total + 49) // 50)
    page_number = min(max(page_number, 1), total_pages)
    base_url = f"/pairs/{pair.id}/mappings" if pair else "/attention" if attention else "/songs"
    return render("songs.html", pair.name + " · Song mappings" if pair else "Needs attention" if attention else "Song library",
                  "attention" if attention else "songs", pair=pair, songs=selected[(page_number - 1) * 50:page_number * 50],
                  description="Review song mappings, inspect match evidence, and correct destination songs.",
                  state=state, query=query, counts=counts, total_results=total, total_pages=total_pages, page_number=page_number, base_url=base_url)


@app.get("/songs")
def songs(state: str = "all", q: str = "", p: int = 1) -> HTMLResponse:
    return song_page(None, state, q, p)


@app.get("/attention")
def attention(state: str = "all", q: str = "", p: int = 1) -> HTMLResponse:
    return song_page(None, state, q, p, attention=True)


@app.get("/pairs/{pair_id}/mappings")
def mappings(pair_id: str, state: str = "all", q: str = "", p: int = 1) -> HTMLResponse:
    return song_page(get_pair(pair_id), state, q, p)


@app.post("/pairs/{pair_id}/mappings/{source_id}/correct")
def correct_mapping(pair_id: str, source_id: str, url: str = Form(...)) -> RedirectResponse:
    if not runtime.sync:
        raise HTTPException(503, "Provider setup is incomplete")
    pair = get_pair(pair_id)
    try:
        runtime.sync.correct(pair, source_id, song_id(url))
    except Exception as exc:
        logger.exception("Manual correction failed: pair=%s source=%s", pair_id, source_id)
        raise HTTPException(400, str(exc)) from exc
    return RedirectResponse(f"/pairs/{pair_id}/mappings", status_code=303)


@app.post("/pairs/{pair_id}/mappings/{source_id}/flag")
def flag_mapping(pair_id: str, source_id: str) -> RedirectResponse:
    if not runtime.sync:
        raise HTTPException(503, "Provider setup is incomplete")
    try:
        runtime.sync.flag_incorrect(get_pair(pair_id), source_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return RedirectResponse(f"/pairs/{pair_id}/mappings?state=incorrect", status_code=303)


@app.get("/setup/apple")
def apple_setup() -> HTMLResponse:
    token = runtime.settings.load().get("apple", {}).get("developer_token", "")
    return render("apple.html", "Apple Music authorization", "settings", token=token,
                  token_json=json.dumps(token).replace("<", "\\u003c"))


@app.post("/setup/apple")
def save_apple_token(user_token: str = Form(...)) -> RedirectResponse:
    data = runtime.settings.load()
    try:
        AppleMusicClient(data["apple"]["developer_token"], user_token.strip()).storefront()
    except Exception as exc:
        logger.exception("Apple Music authorization verification failed")
        raise HTTPException(400, str(exc)) from exc
    data["apple"]["music_user_token"] = user_token.strip()
    runtime.settings.save(data)
    runtime.rebuild()
    return RedirectResponse("/", status_code=303)


@app.get("/settings")
def settings_page() -> HTMLResponse:
    return render("settings.html", "Settings & connections", "settings", config=runtime.settings.load())


@app.post("/settings")
def save_settings(developer_token: str = Form(""), webhook_url: str = Form(""), schedule_time: str = Form("03:00"), timezone: str = Form("Asia/Shanghai"), schedule_enabled: str | None = Form(None)) -> RedirectResponse:
    data = runtime.settings.load()
    data["apple"]["developer_token"] = developer_token.strip()
    data["discord"]["webhook_url"] = webhook_url.strip()
    data["schedule"].update(time=schedule_time.strip(), timezone=timezone.strip(), enabled=schedule_enabled is not None)
    try:
        ZoneInfo(timezone.strip())
        runtime.settings.save(data)
    except (SettingsError, ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc
    runtime.rebuild()
    return RedirectResponse("/settings", status_code=303)


@app.post("/setup/netease")
def netease_setup() -> RedirectResponse:
    try:
        client = PyncmClient()
        data = runtime.settings.load()
        data["netease"]["session"] = client.login_qr()
        runtime.settings.save(data)
        runtime.rebuild()
    except Exception as exc:
        logger.exception("Netease QR login failed")
        raise HTTPException(500, str(exc)) from exc
    return RedirectResponse("/settings", status_code=303)
