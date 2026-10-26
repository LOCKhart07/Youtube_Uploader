"""When the tray app should auto-upload or remind - pure timing logic.

The app asks `Schedule` once a minute. A daily slot that passed while the app
wasn't running (PC off, app closed) is caught up once, the next time it is -
never more than once per missed day. Last-run times live in state.json in the
data folder, so they survive restarts.

Turning a rule on, or changing it, starts it fresh from "now": a daily upload
set at 10:00 for 03:00 first runs tomorrow at 03:00, not immediately.
"""

from __future__ import annotations

import json
from datetime import datetime, time, timedelta
from pathlib import Path

from .config import PROJECT_ROOT, ScheduleConfig

STATE_FILE = PROJECT_ROOT / "state.json"


def last_slot(now: datetime, at: time) -> datetime:
    """Most recent `at` o'clock at or before `now`."""
    slot = datetime.combine(now.date(), at)
    return slot if slot <= now else slot - timedelta(days=1)


def next_slot(now: datetime, at: time) -> datetime:
    """First `at` o'clock after `now`."""
    slot = datetime.combine(now.date(), at)
    return slot if slot > now else slot + timedelta(days=1)


def _upload_rule(cfg: ScheduleConfig) -> str | None:
    if cfg.auto_upload == "daily":
        return f"daily@{cfg.auto_upload_time:%H:%M}"
    if cfg.auto_upload == "interval":
        return f"every{cfg.auto_upload_every_hours}h"
    return None


def _remind_rule(cfg: ScheduleConfig) -> str | None:
    return f"daily@{cfg.remind_time:%H:%M}" if cfg.remind else None


class Schedule:
    def __init__(self, state_file: Path = STATE_FILE):
        self.state_file = state_file
        try:
            self.state: dict = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.state = {}

    # ------------------------------------------------------------- state

    def _save(self) -> None:
        try:
            self.state_file.write_text(json.dumps(self.state, indent=2), encoding="utf-8")
        except OSError:
            pass  # worst case a missed slot is caught up again next start

    def _last(self, kind: str, rule: str, now: datetime) -> datetime:
        """Last run of `kind`; (re)starts from now when the rule is new."""
        if self.state.get(f"{kind}_rule") != rule or f"last_{kind}" not in self.state:
            self.state[f"{kind}_rule"] = rule
            self.state[f"last_{kind}"] = now.isoformat(timespec="seconds")
            self._save()
        return datetime.fromisoformat(self.state[f"last_{kind}"])

    def _mark(self, kind: str, now: datetime) -> None:
        self.state[f"last_{kind}"] = now.isoformat(timespec="seconds")
        self._save()

    # ----------------------------------------------------------- uploads

    def next_upload(self, cfg: ScheduleConfig, now: datetime) -> datetime | None:
        """When the next automatic upload runs (<= now means due), or None if off."""
        rule = _upload_rule(cfg)
        if rule is None:
            return None
        last = self._last("upload", rule, now)
        if cfg.auto_upload == "interval":
            return last + timedelta(hours=cfg.auto_upload_every_hours)
        if last < last_slot(now, cfg.auto_upload_time):
            return last_slot(now, cfg.auto_upload_time)  # missed - due now
        return next_slot(now, cfg.auto_upload_time)

    def upload_due(self, cfg: ScheduleConfig, now: datetime) -> bool:
        nxt = self.next_upload(cfg, now)
        return nxt is not None and nxt <= now

    def mark_upload(self, now: datetime) -> None:
        self._mark("upload", now)

    # --------------------------------------------------------- reminders

    def next_reminder(self, cfg: ScheduleConfig, now: datetime) -> datetime | None:
        rule = _remind_rule(cfg)
        if rule is None:
            return None
        last = self._last("reminder", rule, now)
        if last < last_slot(now, cfg.remind_time):
            return last_slot(now, cfg.remind_time)
        return next_slot(now, cfg.remind_time)

    def reminder_due(self, cfg: ScheduleConfig, now: datetime) -> bool:
        nxt = self.next_reminder(cfg, now)
        return nxt is not None and nxt <= now

    def mark_reminder(self, now: datetime) -> None:
        self._mark("reminder", now)
