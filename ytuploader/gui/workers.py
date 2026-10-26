"""Run service workflows off the UI thread and relay their progress as Qt signals.

Playwright's sync API and the OAuth flow both block, so every workflow runs in
a `Task` (QThread). `QtEvents` is the service `Events` sink for the app: its
signals are emitted from the worker thread and delivered to UI slots on the
main thread by Qt's queued connections.
"""

from __future__ import annotations

import traceback
from typing import Callable

from PySide6.QtCore import QObject, QThread, Signal

from ..service import Events


class QtEvents(QObject, Events):
    log_line = Signal(str)
    started = Signal(int, int, str, str, str)   # index, total, folder, name, size
    done = Signal(str, str, int)                # name, url, count
    failed = Signal(str, str)                   # name, message

    def log(self, message: str) -> None:
        self.log_line.emit(message)

    def video_started(self, index: int, total: int, folder: str, name: str, size: str) -> None:
        self.started.emit(index, total, folder, name, size)
        super().video_started(index, total, folder, name, size)

    def video_done(self, name: str, url: str, count: int) -> None:
        self.done.emit(name, url, count)
        super().video_done(name, url, count)

    def video_failed(self, name: str, message: str) -> None:
        self.failed.emit(name, message)
        super().video_failed(name, message)


class Task(QThread):
    """Runs `fn()` once in a background thread."""

    succeeded = Signal(object)
    errored = Signal(str, str)  # short message, traceback

    def __init__(self, name: str, fn: Callable[[], object], parent=None):
        super().__init__(parent)
        self.name = name
        self._fn = fn

    def run(self) -> None:
        try:
            self.succeeded.emit(self._fn())
        except BaseException as e:  # noqa: BLE001 - surface everything in the UI
            self.errored.emit(str(e) or type(e).__name__, traceback.format_exc())
