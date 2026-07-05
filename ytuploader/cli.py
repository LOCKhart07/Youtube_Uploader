"""Command line entry point.

    python -m ytuploader login      one-time: log in to YouTube in the browser profile
    python -m ytuploader auth       one-time: authorise the API (creates token.json)
    python -m ytuploader scan       sync the captures folder into the DB
    python -m ytuploader playlists  make sure every folder has a YouTube playlist
    python -m ytuploader status     show per-playlist upload progress
    python -m ytuploader upload     upload pending videos (Ctrl+C to stop cleanly)
    python -m ytuploader run        scan + playlists + upload, all in one
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

from .config import Config, load_config
from .db import Database
from .notify import build_report_html, send_report
from .scanner import scan
from .uploader import Status, UploadSession
from .youtube_api import QuotaExceeded, YouTubeAPI


def _playlist_title(cfg: Config, folder_name: str) -> str:
    return cfg.playlist_prefix + str(folder_name).strip().replace("  ", " ")


# --------------------------------------------------------------------- commands

def cmd_login(cfg: Config, _args) -> int:
    """Open a visible browser so you can log in to YouTube once."""
    print(f"Opening a browser using profile: {cfg.browser_profile_dir}")
    print("Log in to your YouTube account, then come back here and press Enter.")
    with UploadSession(
        cfg.browser_profile_dir, headless=False, channel=cfg.browser_channel
    ) as session:
        session._page.goto("https://www.youtube.com")
        input("Press Enter once you are logged in and see your channel... ")
    print("Saved. The profile will stay logged in for future runs.")
    return 0


def cmd_auth(cfg: Config, _args) -> int:
    YouTubeAPI(cfg.client_secrets_file, cfg.token_file)  # triggers the OAuth flow
    print(f"API authorised. Token cached at {cfg.token_file}")
    return 0


def cmd_scan(cfg: Config, _args) -> int:
    db = Database(cfg.db_url)
    new_folders, new_videos, removed_orphans = scan(cfg, db)
    print(f"Scan complete: {new_folders} new folders, {new_videos} new videos.")
    if removed_orphans:
        print(f"Removed {removed_orphans} orphaned entries (moved/deleted source files).")
    _, uploaded, remaining = db.counts()
    print(f"DB now: {uploaded} uploaded, {remaining} remaining.")
    return 0


def cmd_playlists(cfg: Config, _args) -> int:
    db = Database(cfg.db_url)
    api = YouTubeAPI(cfg.client_secrets_file, cfg.token_file)
    _ensure_playlists(cfg, db, api)
    return 0


def cmd_status(cfg: Config, _args) -> int:
    db = Database(cfg.db_url)
    total, uploaded, remaining = db.counts()
    print(f"\nTotal: {total}   Uploaded: {uploaded}   Remaining: {remaining}\n")
    print(f"{'Playlist':<34}{'Up':>6}{'Rem':>6}{'Tot':>6}")
    print("-" * 52)
    for d in db.distribution():
        print(f"{d['playlist'][:33]:<34}{d['uploaded']:>6}{d['remaining']:>6}{d['total']:>6}")
    irregular = db.irregular_videos()
    if irregular:
        print(f"\n{len(irregular)} videos in an improper state (uploaded but no youtube id).")
    return 0


def cmd_upload(cfg: Config, args) -> int:
    db = Database(cfg.db_url)

    pending = db.pending_videos(order=cfg.order)
    if not pending:
        print("Nothing to upload - all caught up.")
        return 0

    # The playlist API is optional: if it isn't set up, still upload the videos
    # but skip adding them to playlists (with a clear warning).
    api = None
    playlists: dict = {}
    try:
        api = YouTubeAPI(cfg.client_secrets_file, cfg.token_file)
        playlists = _ensure_playlists(cfg, db, api, only_needed_for=pending)
    except Exception as e:  # noqa: BLE001
        print(f"[playlists disabled] {str(e).splitlines()[0]}")
        print("  Uploads will proceed but will NOT be added to playlists.")
        print("  Run `python -m ytuploader auth` after setting up a Desktop OAuth")
        print("  client to enable playlist assignment.\n")

    cap = cfg.max_uploads_per_run
    print(f"{len(pending)} videos pending. Visibility={cfg.visibility}. "
          f"{'Cap ' + str(cap) if cap else 'No cap'}. Press Ctrl+C to stop cleanly.\n")

    uploaded_this_run = 0
    stop_reason = "finished"

    try:
        with UploadSession(
            cfg.browser_profile_dir, headless=cfg.headless, channel=cfg.browser_channel
        ) as session:
            for video, folder in pending:
                if cap and uploaded_this_run >= cap:
                    stop_reason = f"hit per-run cap ({cap})"
                    break

                title, description, tags = _video_metadata(cfg, video, folder)
                print(f"  -> [{folder.name}] {video.video_name} ({_human_size(video.video_path)})")
                result = session.upload(
                    video.video_path, title, description, tags, cfg.visibility
                )

                if result.status is Status.NOT_LOGGED_IN:
                    print("\nThe browser profile is not logged in. "
                          "Run `python -m ytuploader login` first.")
                    stop_reason = "not logged in"
                    break

                if result.status is Status.DAILY_LIMIT:
                    print("     YouTube daily upload limit reached - stopping.")
                    stop_reason = "daily upload limit reached"
                    break

                if not result.ok:
                    print(f"     FAILED: {result.message}")
                    continue  # skip this one, keep going

                # Add to the matching playlist via the (cheap) API, if enabled.
                pl_title = _playlist_title(cfg, folder.name)
                pl_id = playlists.get(pl_title)
                if api and pl_id:
                    try:
                        api.add_to_playlist(result.video_id, pl_id)
                    except QuotaExceeded:
                        print("     (playlist add skipped - API quota spent for today)")
                    except Exception as e:  # noqa: BLE001
                        print(f"     (playlist add failed: {e})")

                db.mark_uploaded(video.video_id, result.video_id)
                uploaded_this_run += 1
                print(f"     OK https://youtu.be/{result.video_id}  "
                      f"({uploaded_this_run} this run)")

    except KeyboardInterrupt:
        stop_reason = "stopped by user (Ctrl+C)"
        print("\nStopping cleanly...")

    _finish(cfg, db, uploaded_this_run, stop_reason)
    return 0


def cmd_run(cfg: Config, args) -> int:
    print("== scan ==")
    cmd_scan(cfg, args)
    print("\n== upload ==")
    return cmd_upload(cfg, args)


# ---------------------------------------------------------------------- helpers

def _human_size(path: str) -> str:
    try:
        size = float(os.path.getsize(path))
    except OSError:
        return "size n/a"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _video_metadata(cfg: Config, video, folder) -> tuple[str, str, list[str]]:
    import ast
    from datetime import datetime

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


def _ensure_playlists(cfg: Config, db: Database, api: YouTubeAPI, only_needed_for=None) -> dict:
    """Make sure a YouTube playlist exists for every (relevant) folder.

    Returns the {playlist_title: playlist_id} map.
    """
    print("Fetching playlists from YouTube...")
    existing = api.list_playlists()

    if only_needed_for is not None:
        needed = {_playlist_title(cfg, folder.name) for _video, folder in only_needed_for}
    else:
        with db.session() as s:
            from .db import AppFolder
            needed = {
                _playlist_title(cfg, name)
                for (name,) in s.query(AppFolder.name).all()
            }

    created = 0
    for title in sorted(needed):
        if title not in existing:
            try:
                existing[title] = api.create_playlist(title, privacy=cfg.visibility)
                created += 1
                print(f"  created playlist: {title}")
            except QuotaExceeded:
                print("  API quota spent - cannot create more playlists today.")
                break
    print(f"Playlists ready ({created} created, {len(existing)} total).")
    return existing


def _finish(cfg: Config, db: Database, uploaded: int, reason: str) -> None:
    print(f"\nRun {reason}. Uploaded {uploaded} videos.")

    if cfg.notify.enabled:
        print("Sending email summary...")
        try:
            html = build_report_html(uploaded, db.distribution(), db.irregular_videos())
            send_report(
                cfg.notify,
                subject=f"YT Uploader: {uploaded} uploaded ({reason})",
                html_body=html,
            )
            print("Email sent.")
        except Exception as e:  # noqa: BLE001
            print(f"Email failed: {e}")

    if cfg.shutdown_when_done:
        print("Shutting down the system (configured).")
        subprocess.run(["shutdown", "-s", "-t", "60"])


# ------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ytuploader", description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("login", "auth", "scan", "playlists", "status", "upload", "run"):
        sub.add_parser(name)

    args = parser.parse_args(argv)
    cfg = load_config(args.config)

    commands = {
        "login": cmd_login,
        "auth": cmd_auth,
        "scan": cmd_scan,
        "playlists": cmd_playlists,
        "status": cmd_status,
        "upload": cmd_upload,
        "run": cmd_run,
    }
    return commands[args.command](cfg, args)


if __name__ == "__main__":
    sys.exit(main())
