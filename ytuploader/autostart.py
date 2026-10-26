"""Start with Windows: a per-user HKCU ...\\CurrentVersion\\Run entry.

Only the packaged .exe can register itself - a source checkout has no stable
command line to launch. The installer writes the same value (its "Start with
Windows" task), so the app and the installer agree on what's enabled.
"""

from __future__ import annotations

import sys

from .config import FROZEN

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "ytuploader"


def supported() -> bool:
    return sys.platform == "win32" and FROZEN


def command() -> str:
    return f'"{sys.executable}" --minimized'


def current() -> str | None:
    """The registered command line, or None when start-with-Windows is off."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _type = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value)
    except OSError:
        return None


def is_enabled() -> bool:
    return current() is not None


def set_enabled(enabled: bool) -> None:
    if not supported():
        raise RuntimeError("Start with Windows needs the installed ytuploader.exe.")
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
            except FileNotFoundError:
                pass
