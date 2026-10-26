"""Core workflows - scan, playlists, upload, login, auth - shared by the CLI and the app.

Progress is reported through an `Events` object rather than print(), and a
running upload stops cleanly (after the current video) once `stop` is set.
The default `Events` prints to the console, which is what the CLI uses.
"""

from __future__ import annotations

import ast
import os
import subprocess
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .config import Config
from .db import AppFolder, Database
from .notify import build_report_html, send_report
from .scanner import scan
from .uploader import Status, UploadSession
from .youtube_api import QuotaExceeded, YouTubeAPI


class Events:
    """Progress sink. Prints to the console by default; the app overrides it."""

    def log(self, message: str) -> None:
        print(message)

    def video_started(self, index: int, total: int, folder: str, name: str, size: str) -> None:
        self.log(f"  -> [{folder}] {name} ({size})")

    def video_done(self, name: str, url: str, count: int) -> None:
        self.log(f"     OK {url}  ({count} this run)")

    def video_failed(self, name: str, message: str) -> None:
        self.log(f"     FAILED: {message}")


@dataclass
class ScanResult:
    new_folders: int
    new_videos: int
    removed_orphans: int
    uploaded: int
    remaining: int


@dataclass
class UploadSummary:
    uploaded: int
    reason: str


@dataclass
class SetupState:
    profile_exists: bool
    client_secrets_exists: bool
    token_exists: bool


def playlist_title(cfg: Config, folder_name: str) -> str:
    return cfg.playlist_prefix + str(folder_name).strip().replace("  ", " ")


def setup_state(cfg: Config) -> SetupState:
    """Cheap, offline check of which one-time setup steps look done."""
    profile = cfg.browser_profile_dir
    return SetupState(
        profile_exists=profile.exists() and any(profile.iterdir()),
        client_secrets_exists=cfg.client_secrets_file.exists(),
        token_exists=cfg.token_file.exists(),
    )


# ------------------------------------------------------------------ workflows

def login(cfg: Config, done: threading.Event | None = None, events: Events | None = None) -> None:
    """Open a visible browser so the user can log in to YouTube once.

    With `done` unset this waits for Enter on the console (CLI). Otherwise it
    waits until `done` is set or the user closes the browser window (app).
    """
    from playwright.sync_api import Error as PlaywrightError

    events = events or Events()
    events.log(f"Opening a browser using profile: {cfg.browser_profile_dir}")
    with UploadSession(
        cfg.browser_profile_dir, headless=False, channel=cfg.browser_channel
    ) as session:
        page = session._page
        page.goto("https://www.youtube.com")
        if done is None:
            input("Log in to YouTube, then press Enter here once you see your channel... ")
        else:
            events.log("Log in to YouTube in the browser window, then click Done.")
            try:
                # wait_for_timeout keeps Playwright's event loop pumping, so a
                # window the user closed is noticed via is_closed().
                while not done.is_set() and not page.is_closed():
                    page.wait_for_timeout(500)
            except PlaywrightError:
                pass  # browser closed underneath us - the profile is saved anyway
    events.log("Saved. The profile will stay logged in for future runs.")


def authorize(cfg: Config, events: Events | None = None) -> Path:
    """Run (or refresh) the API OAuth flow; returns the token path."""
    events = events or Events()
    YouTubeAPI(cfg.client_secrets_file, cfg.token_file)  # triggers the OAuth flow
    events.log(f"API authorised. Token cached at {cfg.token_file}")
    return cfg.token_file


def run_scan(cfg: Config, events: Events | None = None) -> ScanResult:
    events = events or Events()
    db = Database(cfg.db_url)
    new_folders, new_videos, removed_orphans = scan(cfg, db, log=events.log)
    events.log(f"Scan complete: {new_folders} new folders, {new_videos} new videos.")
    if removed_orphans:
        events.log(f"Removed {removed_orphans} orphaned entries (moved/deleted source files).")
    _, uploaded, remaining = db.counts()
    events.log(f"DB now: {uploaded} uploaded, {remaining} remaining.")
    return ScanResult(new_folders, new_videos, removed_orphans, uploaded, remaining)


def ensure_playlists(
    cfg: Config, db: Database, api: YouTubeAPI, events: Events | None = None,
    only_needed_for=None,
) -> dict:
    """Make sure a YouTube playlist exists for every (relevant) folder.

    Returns the {playlist_title: playlist_id} map.
    """
    events = events or Events()
    events.log("Fetching playlists from YouTube...")
    existing = api.list_playlists()

    if only_needed_for is not None:
        needed = {playlist_title(cfg, folder.name) for _video, folder in only_needed_for}
    else:
        with db.session() as s:
            needed = {
                playlist_title(cfg, name)
                for (name,) in s.query(AppFolder.name).all()
            }

    created = 0
    for title in sorted(needed):
        if title not in existing:
            try:
                existing[title] = api.create_playlist(title, privacy=cfg.visibility)
                created += 1
                events.log(f"  created playlist: {title}")
            except QuotaExceeded:
                events.log("  API quota spent - cannot create more playlists today.")
                break
    events.log(f"Playlists ready ({created} created, {len(existing)} total).")
    return existing


def run_upload(
    cfg: Config, events: Events | None = None, stop: threading.Event | None = None
) -> UploadSummary:
    """Upload every pending video until done, stopped, capped or rate-limited."""
    events = events or Events()
    stop = stop or threading.Event()
    db = Database(cfg.db_url)

    pending = db.pending_videos(order=cfg.order)
    if not pending:
        events.log("Nothing to upload - all caught up.")
        return UploadSummary(0, "nothing to upload")

    # The playlist API is optional: if it isn't set up, still upload the videos
    # but skip adding them to playlists (with a clear warning).
    api = None
    playlists: dict = {}
    try:
        api = YouTubeAPI(cfg.client_secrets_file, cfg.token_file)
        playlists = ensure_playlists(cfg, db, api, events, only_needed_for=pending)
    except Exception as e:  # noqa: BLE001
        events.log(f"[playlists disabled] {str(e).splitlines()[0]}")
        events.log("  Uploads will proceed but will NOT be added to playlists.")
        events.log("  Authorise the API (Setup) with a Desktop OAuth client to enable")
        events.log("  playlist assignment.\n")

    cap = cfg.max_uploads_per_run
    total = min(len(pending), cap) if cap else len(pending)
    events.log(f"{len(pending)} videos pending. Visibility={cfg.visibility}. "
               f"{'Cap ' + str(cap) if cap else 'No cap'}.\n")

    uploaded_this_run = 0
    stop_reason = "finished"

    try:
        with UploadSession(
            cfg.browser_profile_dir, headless=cfg.headless, channel=cfg.browser_channel
        ) as session:
            for video, folder in pending:
                if stop.is_set():
                    stop_reason = "stopped by user"
                    break
                if cap and uploaded_this_run >= cap:
                    stop_reason = f"hit per-run cap ({cap})"
                    break

                title, description, tags = video_metadata(video, folder)
                events.video_started(uploaded_this_run + 1, total, folder.name,
                                     video.video_name, human_size(video.video_path))
                result = session.upload(
                    video.video_path, title, description, tags, cfg.visibility
                )

                if result.status is Status.NOT_LOGGED_IN:
                    events.log("\nThe browser profile is not logged in. "
                               "Log in to YouTube first (Setup / `ytuploader login`).")
                    stop_reason = "not logged in"
                    break

                if result.status is Status.DAILY_LIMIT:
                    events.log("     YouTube daily upload limit reached - stopping.")
                    stop_reason = "daily upload limit reached"
                    break

                if not result.ok:
                    events.video_failed(video.video_name, result.message)
                    continue  # skip this one, keep going

                # Add to the matching playlist via the (cheap) API, if enabled.
                pl_id = playlists.get(playlist_title(cfg, folder.name))
                if api and pl_id:
                    try:
                        api.add_to_playlist(result.video_id, pl_id)
                    except QuotaExceeded:
                        events.log("     (playlist add skipped - API quota spent for today)")
                    except Exception as e:  # noqa: BLE001
                        events.log(f"     (playlist add failed: {e})")

                db.mark_uploaded(video.video_id, result.video_id)
                uploaded_this_run += 1
                events.video_done(video.video_name, f"https://youtu.be/{result.video_id}",
                                  uploaded_this_run)

    except KeyboardInterrupt:
        stop_reason = "stopped by user (Ctrl+C)"
        events.log("\nStopping cleanly...")

    _finish(cfg, db, uploaded_this_run, stop_reason, events)
    return UploadSummary(uploaded_this_run, stop_reason)


# -------------------------------------------------------------------- helpers

def human_size(path: str) -> str:
    try:
        size = float(os.path.getsize(path))
    except OSError:
        return "size n/a"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def video_metadata(video, folder) -> tuple[str, str, list[str]]:
    try:
        tags = ast.literal_eval(video.tags) if video.tags else []
    except (ValueError, SyntaxError):
        tags = []

    recorded = video.timestamp
    try:
        recorded = datetime.strptime(video.timestamp, "%Y-%m-%d %H:%M:%S.%f").strftime(
            "%b %d %Y, %I:%M %p"
        )
    except (ValueError, TypeError):
        pass

    description = (
        f"App: {folder.name}\n"
        f"Recorded at: {recorded}\n\n\n\n\n"
        f"Tags: {', '.join(tags)}"
    )
    return video.video_name, description, list(tags)


def _finish(cfg: Config, db: Database, uploaded: int, reason: str, events: Events) -> None:
    events.log(f"\nRun {reason}. Uploaded {uploaded} videos.")

    if cfg.notify.enabled:
        events.log("Sending email summary...")
        try:
            html = build_report_html(uploaded, db.distribution(), db.irregular_videos())
            send_report(
                cfg.notify,
                subject=f"YT Uploader: {uploaded} uploaded ({reason})",
                html_body=html,
            )
            events.log("Email sent.")
        except Exception as e:  # noqa: BLE001
            events.log(f"Email failed: {e}")

    if cfg.shutdown_when_done:
        events.log("Shutting down the system in 60s (configured). Run `shutdown -a` to cancel.")
        subprocess.run(["shutdown", "-s", "-t", "60"])
