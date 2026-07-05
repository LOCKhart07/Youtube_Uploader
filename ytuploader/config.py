"""Load settings from config.toml + secrets from .env.

Paths in the TOML may be relative; they are resolved against the project root
(the folder containing config.toml).
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # dotenv is optional; only needed for email notifications
    def load_dotenv(*_args, **_kwargs):  # type: ignore
        return False


PROJECT_ROOT = Path(__file__).resolve().parent.parent


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


def load_config(path: str | os.PathLike = "config.toml") -> Config:
    """Read config.toml (falling back to config.example.toml) and .env."""
    load_dotenv(PROJECT_ROOT / ".env")

    config_path = _resolve(path)
    if not config_path.exists():
        example = PROJECT_ROOT / "config.example.toml"
        raise FileNotFoundError(
            f"Config not found at {config_path}.\n"
            f"Copy {example.name} to config.toml and edit it for your machine."
        )

    with open(config_path, "rb") as fh:
        data = tomllib.load(fh)

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
