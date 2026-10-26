"""Command line entry point.

    python -m ytuploader login      one-time: log in to YouTube in the browser profile
    python -m ytuploader auth       one-time: authorise the API (creates token.json)
    python -m ytuploader scan       sync the captures folder into the DB
    python -m ytuploader playlists  make sure every folder has a YouTube playlist
    python -m ytuploader status     show per-playlist upload progress
    python -m ytuploader upload     upload pending videos (Ctrl+C to stop cleanly)
    python -m ytuploader run        scan + playlists + upload, all in one
    python -m ytuploader gui        open the desktop app (same as ytuploader.exe)
    python -m ytuploader import DIR copy config/history/tokens/profile from DIR
"""

from __future__ import annotations

import argparse
import sys

from . import service
from .config import Config, load_config
from .db import Database
from .youtube_api import YouTubeAPI


# --------------------------------------------------------------------- commands

def cmd_login(cfg: Config, _args) -> int:
    service.login(cfg)
    return 0


def cmd_auth(cfg: Config, _args) -> int:
    service.authorize(cfg)
    return 0


def cmd_scan(cfg: Config, _args) -> int:
    service.run_scan(cfg)
    return 0


def cmd_playlists(cfg: Config, _args) -> int:
    db = Database(cfg.db_url)
    api = YouTubeAPI(cfg.client_secrets_file, cfg.token_file)
    service.ensure_playlists(cfg, db, api)
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


def cmd_upload(cfg: Config, _args) -> int:
    print("Press Ctrl+C to stop cleanly.")
    service.run_upload(cfg)
    return 0


def cmd_run(cfg: Config, args) -> int:
    print("== scan ==")
    cmd_scan(cfg, args)
    print("\n== upload ==")
    return cmd_upload(cfg, args)


# ------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ytuploader", description=__doc__,
                                      formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("login", "auth", "scan", "playlists", "status", "upload", "run", "gui"):
        sub.add_parser(name)
    imp = sub.add_parser("import", help="copy config/history/tokens from another folder")
    imp.add_argument("folder", help="folder that contains the old config.toml")

    args = parser.parse_args(argv)

    if args.command == "import":
        from .migrate import import_folder
        print(import_folder(args.folder).summary())
        return 0

    if args.command == "gui":
        from .gui.app import main as gui_main
        return gui_main(["--config", args.config])

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
