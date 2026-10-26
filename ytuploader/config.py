"""Load settings from config.toml + secrets from .env.

Paths in the TOML may be relative; they are resolved against the project root
(the folder containing config.toml). In the packaged .exe that root is the
folder the .exe sits in.
"""

from __future__ import annotations

import json
import os
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv is optional; only needed for email notifications
    def load_dotenv(*_args, **_kwargs):  # type: ignore
        return False


def _project_root() -> Path:
    if getattr(sys, "frozen", False):  # PyInstaller .exe
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = _project_root()
# Read-only files shipped with the app (config.example.toml). The one-file exe
# unpacks them to a temp dir exposed as sys._MEIPASS.
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS", PROJECT_ROOT))


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

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path.as_posix()}"


def config_path(path: str | os.PathLike = "config.toml") -> Path:
    return _resolve(path)


def example_config_path() -> Path:
    local = PROJECT_ROOT / "config.example.toml"
    return local if local.exists() else BUNDLE_ROOT / "config.example.toml"


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

    return Config(
        captures_folder=_resolve(paths.get("captures_folder", "captures")),
        db_path=_resolve(paths.get("db_path", "video_db_2.db")),
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
    )


# ------------------------------------------------------------------ writing

_SECTION_ORDER = ("paths", "scan", "upload", "notify", "power")


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
