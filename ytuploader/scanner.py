"""Scan the captures folder and sync it into the tracking DB.

Each immediate subfolder of the captures root is treated as one "app" (game)
and maps to one YouTube playlist. Every .mp4 found underneath it becomes a Video
row (deduplicated by absolute path).
"""

from __future__ import annotations

import datetime
import os
import re
from pathlib import Path

from .config import Config
from .db import Database

# NVIDIA ShadowPlay: "Apex Legends 2020.04.08 - 16.56.44.02.DVR.mp4"
_NVIDIA_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2}) - (\d{2})\.(\d{2})\.(\d{2})\.(\d{2})")
# AMD Adrenalin:      "Hollow Knight Silksong_replay_2025.09.14-01.35.mp4"
_AMD_RE = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})-(\d{2})\.(\d{2})")
# A folder Windows uses for temp recording chunks, e.g. "{9A455DAB-...}".
_GUID_RE = re.compile(r"^\{[0-9A-Fa-f-]+\}$")

# AMD wraps the real title in noise: "Game_replay_2025.09.14-01.35" / "..._1".
_AMD_NAME_RE = re.compile(
    r"^(?P<title>.+?)_replay_(?P<ts>\d{4}\.\d{2}\.\d{2}-\d{2}\.\d{2})(?:_(?P<n>\d+))?$"
)


def _timestamp_for(video_path: str) -> datetime.datetime:
    """Pull the capture time from the filename, else fall back to file ctime.

    Handles both the NVIDIA and AMD filename conventions; manually-renamed
    clips with no timestamp fall back to the file's creation time.
    """
    name = os.path.basename(video_path)

    m = _NVIDIA_RE.search(name)
    if m:
        year, month, day, hour, minute, second, centi = map(int, m.groups())
        return datetime.datetime(year, month, day, hour, minute, second, centi * 1000)

    m = _AMD_RE.search(name)
    if m:
        year, month, day, hour, minute = map(int, m.groups())
        return datetime.datetime(year, month, day, hour, minute)

    stats = os.stat(video_path)
    born = getattr(stats, "st_birthtime", None) or stats.st_mtime
    return datetime.datetime.fromtimestamp(born)


def _clean_title(file_name_no_ext: str) -> str:
    """Tidy AMD's '_replay_' noise into "Game 2025.09.14-01.35" while keeping the
    timestamp so clips stay unique. Other names are left untouched."""
    m = _AMD_NAME_RE.match(file_name_no_ext)
    if not m:
        return file_name_no_ext
    title = f"{m.group('title')} {m.group('ts')}"
    if m.group("n"):
        title += f" ({m.group('n')})"
    return title


def _is_ignored_dir(name: str, ignore: set[str]) -> bool:
    return name in ignore or bool(_GUID_RE.match(name)) or name == "temp"


def scan(config: Config, db: Database) -> tuple[int, int, int]:
    """Walk the captures tree, inserting any new folders/videos.

    Also drops pending rows whose file no longer exists on disk - this
    happens when a clip gets moved to a different (correctly-attributed)
    folder after the initial scan, since videos are deduped by absolute path.

    Returns (new_folders, new_videos, removed_orphans).
    """
    root = config.captures_folder
    if not root.exists():
        raise FileNotFoundError(f"Captures folder not found: {root}")

    ignore = set(config.scan_ignore_folders)
    new_folders = 0
    new_videos = 0
    skipped_folders = []

    for entry in os.scandir(root):
        if not entry.is_dir():
            continue
        # Skip AMD's cache/temp folders so they don't become bogus playlists.
        if _is_ignored_dir(entry.name, ignore):
            skipped_folders.append(entry.name)
            continue

        before = _folder_existed(db, entry.name)
        folder_id = db.get_or_create_folder(entry.name, entry.path)
        if not before:
            new_folders += 1

        for sub_root, dirs, files in os.walk(entry.path):
            # Prune ignored subdirectories in place so os.walk won't descend.
            dirs[:] = [d for d in dirs if not _is_ignored_dir(d, ignore)]
            for file in files:
                if not file.lower().endswith(".mp4"):
                    continue
                video_path = os.path.join(sub_root, file)
                if db.video_exists(video_path):
                    continue

                rel_parts = Path(os.path.relpath(video_path, root)).parts[:-1]
                db.add_video(
                    app_folder_id=folder_id,
                    video_name=_clean_title(os.path.splitext(file)[0]),
                    video_path=video_path,
                    is_uploaded=False,
                    youtube_id="",
                    tags=str(list(rel_parts)),
                    timestamp=str(_timestamp_for(video_path)),
                )
                new_videos += 1

    if skipped_folders:
        print(f"Skipped {len(skipped_folders)} cache/system folders: "
              f"{', '.join(sorted(skipped_folders))}")

    removed_orphans = db.remove_orphaned_pending()

    return new_folders, new_videos, removed_orphans


def _folder_existed(db: Database, name: str) -> bool:
    from .db import AppFolder

    with db.session() as s:
        return s.query(AppFolder).filter(AppFolder.name == name).first() is not None
