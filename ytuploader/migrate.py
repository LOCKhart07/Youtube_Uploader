"""One-time import of an existing ytuploader folder into the data folder.

Used when the installed app starts with an empty %LOCALAPPDATA%/ytuploader and
the user's history, tokens and browser profile still live in the old project
(or portable .exe) folder. Everything is copied - the source folder is never
modified - and an existing upload-history DB in the data folder is never
overwritten: the imported one gets a fresh name instead.
"""

from __future__ import annotations

import shutil
import sqlite3
import tomllib
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .config import PROJECT_ROOT, write_config

# Clean names for the files we bring over (the source may use legacy names
# such as final/video_db_2.db).
_TARGET_NAMES = {
    "db_path": "uploads.db",
    "browser_profile_dir": "playwright_profile",
    "client_secrets_file": "client_secrets.json",
    "token_file": "token.json",
}

# Chrome caches regenerate on their own; IndexedDB is YouTube Studio's offline
# data (over a GB) - the login lives in the cookies, which are kept.
_PROFILE_SKIP = shutil.ignore_patterns(
    "Cache", "Code Cache", "GPUCache", "GrShaderCache", "ShaderCache",
    "DawnGraphiteCache", "DawnWebGPUCache", "CacheStorage", "IndexedDB",
    "Crashpad", "SingletonLock", "SingletonCookie", "SingletonSocket", "lockfile",
)


@dataclass
class ImportReport:
    source: Path
    copied: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    config_file: Path | None = None

    def summary(self) -> str:
        lines = [f"Imported from {self.source}:"]
        lines += [f"  + {c}" for c in self.copied]
        lines += [f"  - {s}" for s in self.skipped]
        return "\n".join(lines)


def looks_like_data_folder(folder: Path) -> bool:
    return (folder / "config.toml").is_file()


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return path.with_name(f"{path.stem}-imported-{stamp}{path.suffix}")


def _copy_db(src: Path, dest: Path) -> None:
    """Copy via SQLite's backup API so a DB that's open elsewhere copies consistently."""
    source = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(dest)
    try:
        source.backup(target)
    finally:
        target.close()
        source.close()


def import_folder(source: str | Path, dest: Path = PROJECT_ROOT) -> ImportReport:
    """Copy config, history DB, OAuth files, browser profile and .env from
    `source` into `dest` and write a config.toml there whose relative paths
    point at the copies. Paths outside `source` (e.g. the captures folder) are
    kept as absolute paths."""
    source = Path(source).resolve()
    dest = Path(dest).resolve()
    if source == dest:
        raise ValueError("That is already the data folder.")
    src_config = source / "config.toml"
    if not src_config.is_file():
        raise FileNotFoundError(f"No config.toml in {source}")
    with open(src_config, "rb") as fh:
        data = tomllib.load(fh)

    dest.mkdir(parents=True, exist_ok=True)
    report = ImportReport(source)
    paths = data.setdefault("paths", {})

    def resolve(value: str) -> Path:
        p = Path(value)
        return p if p.is_absolute() else source / p

    if "captures_folder" in paths:
        paths["captures_folder"] = str(resolve(paths["captures_folder"]))

    defaults = {"db_path": "video_db_2.db", "browser_profile_dir": "playwright_profile",
                "client_secrets_file": "client_secrets.json", "token_file": "token.json"}
    for key, target_name in _TARGET_NAMES.items():
        src = resolve(paths.get(key, defaults[key]))
        if not src.is_relative_to(source):
            paths[key] = str(src)  # lives elsewhere already; keep using it there
            report.skipped.append(f"{key}: left in place at {src}")
            continue
        target = dest / target_name
        if key == "db_path":
            target = _unique(target)
        paths[key] = target.name
        if not src.exists():
            report.skipped.append(f"{key}: {src} not found")
            continue
        if key == "db_path":
            _copy_db(src, target)
        elif src.is_dir():
            if target.exists():
                report.skipped.append(f"{key}: {target} already exists, kept it")
                continue
            try:
                shutil.copytree(src, target, ignore=_PROFILE_SKIP)
            except shutil.Error as e:  # locked files (e.g. Chrome still open)
                report.skipped.append(f"{key}: {len(e.args[0])} files could not be copied")
        else:
            if target.exists():
                report.skipped.append(f"{key}: {target} already exists, kept it")
                continue
            shutil.copy2(src, target)
        report.copied.append(f"{src} -> {target}")

    env = source / ".env"
    if env.is_file() and not (dest / ".env").exists():
        shutil.copy2(env, dest / ".env")
        report.copied.append(f"{env} -> {dest / '.env'}")

    existing = dest / "config.toml"
    if existing.exists():
        backup = _unique(existing.with_suffix(".toml.bak"))
        existing.replace(backup)
        report.copied.append(f"previous config saved as {backup.name}")
    report.config_file = write_config(data, dest / "config.toml")
    return report
