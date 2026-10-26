"""Load settings from config.toml + secrets from .env.

Paths in the TOML may be relative; they are resolved against the data folder
(the folder containing config.toml), `PROJECT_ROOT`:

* from source: the repository root;
* portable .exe: the folder the .exe sits in, when a `config.toml` or a
  `portable` marker file is next to it;
* installed .exe: a per-user folder, %LOCALAPPDATA%/ytuploader (the install
  folder isn't meant to be written to).

The YTUPLOADER_HOME environment variable overrides all of these.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
from dataclasses import dataclass, field
from datetime import time as dt_time
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv is optional; only needed for email notifications
    def load_dotenv(*_args, **_kwargs):  # type: ignore
        return False


FROZEN = bool(getattr(sys, "frozen", False))  # running as the PyInstaller .exe
# Where the program itself lives: the .exe's folder, or the repository root.
APP_DIR = (Path(sys.executable).resolve().parent if FROZEN
           else Path(__file__).resolve().parent.parent)


def user_data_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local")
    return Path(base) / "ytuploader"


def _data_root() -> tuple[Path, bool]:
    """Return (data folder, is_portable)."""
    override = os.environ.get("YTUPLOADER_HOME")
    if override:
        return Path(override).expanduser().resolve(), False
    if not FROZEN:
        return APP_DIR, True
    if (APP_DIR / "config.toml").exists() or (APP_DIR / "portable").exists():
        return APP_DIR, True
    return user_data_dir(), False


PROJECT_ROOT, PORTABLE = _data_root()
PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
# Read-only files shipped with the app (config.example.toml). PyInstaller
# exposes its bundle dir (temp dir for one-file, _internal for one-folder) as
# sys._MEIPASS.
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", APP_DIR))


def _resolve(p: str | os.PathLike) -> Path:
    path = Path(p)
    return path if path.is_absolute() else (PROJECT_ROOT / path)


@dataclass(frozen=True)
class NotifyConfig:
    enabled: bool = False
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    from_addr: str = ""
    to_addr: str = ""
    # Secret, pulled from the environment - never from the TOML file.
    password: str = field(default="", repr=False)


@dataclass(frozen=True)
class ScheduleConfig:
    """What the tray app does on its own while it runs (see schedule.py)."""
    auto_upload: str = "off"           # off / daily / interval
    auto_upload_time: dt_time = dt_time(3, 0)
    auto_upload_every_hours: int = 6
    remind: bool = False
    remind_time: dt_time = dt_time(20, 0)
    remind_min_waiting: int = 1


def _parse_time(value, key: str) -> dt_time:
    if isinstance(value, dt_time):  # TOML local-time literal (03:00:00)
        return value
    try:
        return dt_time.fromisoformat(str(value).strip())
    except ValueError:
        raise ValueError(f"{key} must be a time like '03:00', got {value!r}") from None


@dataclass(frozen=True)
class Config:
    # paths
    captures_folder: Path
    db_path: Path
    browser_profile_dir: Path
    client_secrets_file: Path
    token_file: Path
    # scanning
    scan_ignore_folders: tuple[str, ...]
    # upload
    playlist_prefix: str
    visibility: str
    browser_channel: str
    headless: bool
    max_uploads_per_run: int
    order: str
    # notify / power
    notify: NotifyConfig
    shutdown_when_done: bool
    # tray-app automation
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"


def config_path(path: str | os.PathLike = "config.toml") -> Path:
    return _resolve(path)


def example_config_path() -> Path:
    for folder in (PROJECT_ROOT, APP_DIR, BUNDLE_ROOT):
        if (folder / "config.example.toml").exists():
            return folder / "config.example.toml"
    return BUNDLE_ROOT / "config.example.toml"


def read_raw_config(path: str | os.PathLike = "config.toml") -> tuple[dict, bool]:
    """Return (toml_data, exists). Falls back to the example's values when
    config.toml doesn't exist yet, so the app can offer to create it."""
    config_file = _resolve(path)
    source = config_file if config_file.exists() else example_config_path()
    if not source.exists():
        return {}, False
    with open(source, "rb") as fh:
        return tomllib.load(fh), config_file.exists()


def load_config(path: str | os.PathLike = "config.toml") -> Config:
    """Read config.toml and .env."""
    config_path = _resolve(path)
    if not config_path.exists():
        raise FileNotFoundError(
            f"Config not found at {config_path}.\n"
            f"Copy config.example.toml to config.toml and edit it for your machine."
        )

    with open(config_path, "rb") as fh:
        return config_from_dict(tomllib.load(fh))


def config_from_dict(data: dict) -> Config:
    """Validate parsed TOML and build a Config (secrets come from .env)."""
    load_dotenv(PROJECT_ROOT / ".env")

    paths = data.get("paths", {})
    scan = data.get("scan", {})
    upload = data.get("upload", {})

    default_ignore = [
        "replay_cache",
        "gif_replay_cache",
        "InGame_replay_cache",
        "thumb_cache",
    ]
    notify = data.get("notify", {})
    power = data.get("power", {})

    visibility = str(upload.get("visibility", "private")).lower()
    if visibility not in ("private", "unlisted", "public"):
        raise ValueError(f"visibility must be private/unlisted/public, got {visibility!r}")

    order = str(upload.get("order", "fewest_remaining")).lower()
    if order not in ("fewest_remaining", "name", "random"):
        raise ValueError(f"order must be fewest_remaining/name/random, got {order!r}")

    sched = data.get("schedule", {})
    auto_upload = str(sched.get("auto_upload", "off")).lower()
    if auto_upload not in ("off", "daily", "interval"):
        raise ValueError(f"auto_upload must be off/daily/interval, got {auto_upload!r}")
    every_hours = int(sched.get("auto_upload_every_hours", 6))
    if not 1 <= every_hours <= 168:
        raise ValueError(f"auto_upload_every_hours must be 1-168, got {every_hours}")
    schedule = ScheduleConfig(
        auto_upload=auto_upload,
        auto_upload_time=_parse_time(sched.get("auto_upload_time", "03:00"), "auto_upload_time"),
        auto_upload_every_hours=every_hours,
        remind=bool(sched.get("remind", False)),
        remind_time=_parse_time(sched.get("remind_time", "20:00"), "remind_time"),
        remind_min_waiting=max(1, int(sched.get("remind_min_waiting", 1))),
    )

    return Config(
        captures_folder=_resolve(paths.get("captures_folder", "captures")),
        db_path=_resolve(paths.get("db_path", "uploads.db")),
        browser_profile_dir=_resolve(paths.get("browser_profile_dir", "playwright_profile")),
        client_secrets_file=_resolve(paths.get("client_secrets_file", "client_secrets.json")),
        token_file=_resolve(paths.get("token_file", "token.json")),
        scan_ignore_folders=tuple(scan.get("ignore_folders", default_ignore)),
        playlist_prefix=upload.get("playlist_prefix", "CAPTURES - "),
        visibility=visibility,
        browser_channel=str(upload.get("browser_channel", "chrome")),
        headless=bool(upload.get("headless", True)),
        max_uploads_per_run=int(upload.get("max_uploads_per_run", 0)),
        order=order,
        notify=NotifyConfig(
            enabled=bool(notify.get("enabled", False)),
            smtp_host=notify.get("smtp_host", "smtp.gmail.com"),
            smtp_port=int(notify.get("smtp_port", 587)),
            from_addr=notify.get("from_addr", ""),
            to_addr=notify.get("to_addr", ""),
            password=os.environ.get("GMAIL_APP_PASSWORD", ""),
        ),
        shutdown_when_done=bool(power.get("shutdown_when_done", False)),
        schedule=schedule,
    )


# ------------------------------------------------------------------ writing

_SECTION_ORDER = ("paths", "scan", "upload", "schedule", "notify", "power")


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    text = str(value)
    if "'" not in text and "\n" not in text:
        return f"'{text}'"  # literal string: Windows backslashes stay as-is
    return json.dumps(text)  # JSON escapes are valid TOML basic-string escapes


def write_config(data: dict, path: str | os.PathLike = "config.toml") -> Path:
    """Validate and write `data` (same shape as the parsed TOML) to config.toml.

    Comments in an existing file are not preserved - config.example.toml keeps
    the documented version of every setting.
    """
    config_from_dict(data)  # raise before touching the file if it's invalid

    lines = [
        "# ytuploader configuration - written by the ytuploader app.",
        "# See config.example.toml for what each setting does.",
    ]
    sections = list(_SECTION_ORDER) + [k for k in data if k not in _SECTION_ORDER]
    for section in sections:
        values = data.get(section)
        if not isinstance(values, dict):
            continue
        lines.append("")
        lines.append(f"[{section}]")
        for key, value in values.items():
            lines.append(f"{key} = {_toml_value(value)}")

    config_file = _resolve(path)
    config_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return config_file
