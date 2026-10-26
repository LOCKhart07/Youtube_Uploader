"""Main window + tray icon. Entry point for `ytuploader.exe` / `python -m ytuploader.gui`.

The window owns the loaded config and at most one background `Task` at a time
(the browser profile can only be opened by one Chrome at once). Closing the
window hides it to the tray; Quit lives in the tray menu and the File menu.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QAction, QCloseEvent
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QTabWidget,
)

from .. import __version__, autostart, migrate, service
from ..config import (
    PORTABLE,
    PROJECT_ROOT,
    Config,
    config_path,
    load_config,
    read_raw_config,
    write_config,
)
from .icon import app_icon
from .pages import DashboardPage, SettingsPage, SetupPage, UploadPage
from .workers import QtEvents, Task

APP_NAME = "YT Capture Uploader"
_INSTANCE_KEY = f"ytuploader-gui-{getpass.getuser()}"


class MainWindow(QMainWindow):
    def __init__(self, config_arg: str):
        super().__init__()
        self.config_arg = config_arg
        self.cfg: Config | None = None
        self.task: Task | None = None
        self._on_success = None
        self.stop = threading.Event()
        self.login_done = threading.Event()
        self.events = QtEvents()
        self._quitting = False
        self._told_about_tray = False

        self.setWindowTitle(APP_NAME)
        self.setWindowIcon(app_icon())
        self.resize(960, 680)

        self.dashboard = DashboardPage()
        self.upload = UploadPage()
        self.setup = SetupPage()
        self.settings = SettingsPage()
        self.tabs = QTabWidget()
        self.tabs.addTab(self.dashboard, "Dashboard")
        self.tabs.addTab(self.upload, "Upload")
        self.tabs.addTab(self.setup, "Setup")
        self.tabs.addTab(self.settings, "Settings")
        self.setCentralWidget(self.tabs)

        self._build_menu()
        self._build_tray()
        self._wire()
        self.reload_config()

    # ----------------------------------------------------------------- chrome

    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&File")
        file_menu.addAction("Scan + upload now", self.do_run)
        file_menu.addSeparator()
        file_menu.addAction("Import data from another folder...",
                            lambda: self.offer_import(first_run=False))
        file_menu.addAction("Open data folder", lambda: os.startfile(PROJECT_ROOT))
        file_menu.addSeparator()
        file_menu.addAction("Hide to tray", self.hide)
        quit_action = QAction("Quit", self)
        quit_action.setShortcut("Ctrl+Q")
        quit_action.triggered.connect(self.quit)
        file_menu.addAction(quit_action)

        help_menu = self.menuBar().addMenu("&Help")
        help_menu.addAction("About", lambda: QMessageBox.about(
            self, APP_NAME,
            f"<b>{APP_NAME}</b> {__version__}<br><br>"
            "Uploads game captures to YouTube, one playlist per game, "
            "without ever uploading a clip twice.",
        ))

    def _build_tray(self) -> None:
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        menu.addAction("Open", self.show_window)
        self.tray_run = menu.addAction("Scan + upload now", self.do_run)
        self.tray_stop = menu.addAction("Stop upload", self.do_stop)
        self.tray_stop.setEnabled(False)
        menu.addSeparator()
        menu.addAction("Quit", self.quit)
        self.tray.setContextMenu(menu)
        self._tray_menu = menu  # keep a reference; the tray doesn't own it
        self.tray.activated.connect(self._tray_activated)
        self.tray.messageClicked.connect(self.show_window)
        self.tray.show()

    def _wire(self) -> None:
        self.events.log_line.connect(self.upload.append)
        self.events.started.connect(self.upload.video_started)
        self.events.done.connect(self.upload.video_done)

        self.dashboard.refresh_requested.connect(self.refresh)
        self.upload.scan_requested.connect(self.do_scan)
        self.upload.upload_requested.connect(self.do_upload)
        self.upload.run_requested.connect(self.do_run)
        self.upload.stop_requested.connect(self.do_stop)
        self.setup.login_requested.connect(self.do_login)
        self.setup.login_done.connect(self.login_done.set)
        self.setup.auth_requested.connect(self.do_auth)
        self.setup.secrets_chosen.connect(self.install_client_secrets)
        self.settings.saved.connect(self.save_settings)

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def notify(self, title: str, message: str, warn: bool = False) -> None:
        icon = QSystemTrayIcon.Warning if warn else QSystemTrayIcon.Information
        self.tray.showMessage(title, message, icon, 8000)

    # ----------------------------------------------------------------- config

    def reload_config(self) -> None:
        raw, exists = read_raw_config(self.config_arg)
        self.settings.load(raw, exists)
        try:
            self.cfg = load_config(self.config_arg)
        except FileNotFoundError:
            self.cfg = None
            self.statusBar().showMessage("No config.toml yet - set it up in Settings.")
            self.dashboard.show_error("Not configured yet. Open Settings, check the paths and Save.")
            self.tabs.setCurrentWidget(self.settings)
        except Exception as e:  # noqa: BLE001
            self.cfg = None
            self.statusBar().showMessage(f"config.toml is invalid: {e}")
            self.dashboard.show_error(f"config.toml is invalid: {e}")
            self.tabs.setCurrentWidget(self.settings)
        else:
            self.statusBar().showMessage(f"Config: {config_path(self.config_arg)}")
        self.refresh()

    def refresh(self) -> None:
        if self.cfg:
            self.dashboard.refresh(self.cfg)
            self.setup.refresh(service.setup_state(self.cfg))
        else:
            self.setup.refresh(None)

    def save_settings(self, data: dict) -> None:
        try:
            path = write_config(data, self.config_arg)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Could not save", str(e))
            return
        self.reload_config()
        self.statusBar().showMessage(f"Saved {path}", 5000)
        self._apply_autostart(self.settings.autostart.isChecked())

    def _apply_autostart(self, enabled: bool) -> None:
        if not autostart.supported():
            return
        wanted = autostart.command() if enabled else None
        if autostart.current() == wanted:
            return
        try:
            autostart.set_enabled(enabled)
        except OSError as e:
            QMessageBox.warning(self, "Start with Windows", f"Couldn't update the registry: {e}")
        self.settings.autostart.setChecked(autostart.is_enabled())

    def first_run_check(self) -> None:
        """Installed build, empty data folder: offer to bring the old data over."""
        if not PORTABLE and not config_path(self.config_arg).exists():
            self.show_window()
            self.offer_import(first_run=True)

    def offer_import(self, first_run: bool) -> None:
        if self.busy:
            self.notify(APP_NAME, f"Busy: {self.task.name}. Try again when it finishes.")
            return
        if first_run:
            box = QMessageBox(self)
            box.setWindowTitle(APP_NAME)
            box.setIcon(QMessageBox.Question)
            box.setText("Welcome! Import your existing ytuploader data?")
            box.setInformativeText(
                "If you used ytuploader before (from the project folder or the portable "
                "exe), choose that folder - the one with config.toml - to copy your "
                "upload history, settings, YouTube login and API token into "
                f"{PROJECT_ROOT}.\n\nThe original folder is left untouched."
            )
            choose = box.addButton("Choose folder...", QMessageBox.AcceptRole)
            box.addButton("Start fresh", QMessageBox.RejectRole)
            box.exec()
            if box.clickedButton() is not choose:
                return
        elif config_path(self.config_arg).exists():
            answer = QMessageBox.question(
                self, "Import data",
                "This replaces the current settings (they're kept as config.toml.bak). "
                "Your current upload-history DB is not overwritten. Continue?",
            )
            if answer != QMessageBox.Yes:
                return

        folder = QFileDialog.getExistingDirectory(self, "Folder that contains config.toml")
        if not folder:
            return
        if not migrate.looks_like_data_folder(Path(folder)):
            QMessageBox.warning(self, "Nothing to import", f"There's no config.toml in {folder}.")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            report = migrate.import_folder(folder)
        except Exception as e:  # noqa: BLE001
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Import failed", str(e))
            return
        QApplication.restoreOverrideCursor()
        self.upload.append(report.summary())
        self.reload_config()
        self.tabs.setCurrentWidget(self.dashboard)
        QMessageBox.information(self, "Import done", report.summary())

    def install_client_secrets(self, source: str) -> None:
        if not self._require_config():
            return
        try:
            data = json.loads(open(source, encoding="utf-8").read())
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "Not a client file", f"Couldn't read {source}: {e}")
            return
        if "installed" not in data:
            QMessageBox.warning(
                self, "Wrong kind of OAuth client",
                "That file is a '" + next(iter(data), "unknown") + "' client. Create a "
                "'Desktop app' OAuth client in Google Cloud Console and choose its JSON.",
            )
            return
        target = self.cfg.client_secrets_file
        if os.path.abspath(source) != os.path.abspath(target):
            shutil.copyfile(source, target)
        self.upload.append(f"Installed OAuth client file at {target}")
        self.refresh()

    def _require_config(self) -> bool:
        if self.cfg is None:
            QMessageBox.information(self, APP_NAME, "Set up and save Settings first.")
            self.show_window()
            self.tabs.setCurrentWidget(self.settings)
            return False
        return True

    # ------------------------------------------------------------------ tasks

    @property
    def busy(self) -> bool:
        return self.task is not None

    def start_task(self, label: str, fn, on_success=None) -> bool:
        if not self._require_config():
            return False
        if self.busy:
            self.notify(APP_NAME, f"Busy: {self.task.name}. Try again when it finishes.")
            return False

        self.stop.clear()
        self.login_done.clear()
        self._on_success = on_success
        self.task = Task(label, fn, self)
        # Bound methods (not lambdas) so Qt queues these onto the UI thread.
        self.task.succeeded.connect(self._task_succeeded)
        self.task.errored.connect(self._task_failed)
        self.task.finished.connect(self._task_finished)
        self._set_busy(True, label)
        self.upload.append(f"== {label} ==")
        self.task.start()
        return True

    def _set_busy(self, busy: bool, label: str = "") -> None:
        self.upload.set_busy(busy, label)
        self.setup.set_busy(busy, label)
        uploading = busy and label in ("Upload", "Scan + upload")
        self.tray_run.setEnabled(not busy)
        self.tray_stop.setEnabled(uploading)
        self.tray.setIcon(app_icon(busy=busy))
        self.tray.setToolTip(f"{APP_NAME} - {label}..." if busy else APP_NAME)

    def _task_succeeded(self, result) -> None:
        if self._on_success:
            self._on_success(result)

    def _task_failed(self, message: str, tb: str) -> None:
        label = self.task.name if self.task else "Task"
        self.upload.append(f"{label} failed: {message}")
        self.upload.append(tb.rstrip())
        self.upload.finished(f"{label} failed: {message.splitlines()[0]}")
        if self.isVisible():
            QMessageBox.warning(self, f"{label} failed", message)
        else:
            self.notify(f"{label} failed", message.splitlines()[0], warn=True)

    def _task_finished(self) -> None:
        self.task.deleteLater()
        self.task = None
        self._set_busy(False)
        self.refresh()

    def do_scan(self) -> None:
        cfg = self.cfg

        def done(r: service.ScanResult) -> None:
            self.upload.finished(f"Scan done: {r.new_videos} new clips, {r.remaining} waiting.")

        self.start_task("Scan", lambda: service.run_scan(cfg, self.events), done)

    def do_upload(self, scan_first: bool = False) -> None:
        cfg = self.cfg

        def work() -> service.UploadSummary:
            if scan_first:
                service.run_scan(cfg, self.events)
                self.events.log("")
            return service.run_upload(cfg, self.events, self.stop)

        def done(r: service.UploadSummary) -> None:
            text = f"Uploaded {r.uploaded} clip{'s' if r.uploaded != 1 else ''} ({r.reason})."
            self.upload.finished(text)
            if r.uploaded or r.reason != "nothing to upload":
                self.notify("Upload run finished", text,
                            warn=r.reason in ("not logged in",))

        if self.start_task("Scan + upload" if scan_first else "Upload", work, done):
            self.tabs.setCurrentWidget(self.upload)

    def do_run(self) -> None:
        self.do_upload(scan_first=True)

    def do_stop(self) -> None:
        if self.busy:
            self.stop.set()
            self.upload.stopping()
            self.upload.append("Stop requested - finishing the current clip first.")

    def do_login(self) -> None:
        cfg = self.cfg
        self.start_task("Log in", lambda: service.login(cfg, self.login_done, self.events))

    def do_auth(self) -> None:
        cfg = self.cfg
        self.upload.append("Your browser will open to grant playlist access.")
        self.start_task(
            "Authorise API", lambda: service.authorize(cfg, self.events),
            lambda _r: self.statusBar().showMessage("API authorised.", 5000),
        )

    # --------------------------------------------------------------- lifecycle

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._quitting or not QSystemTrayIcon.isSystemTrayAvailable():
            event.accept()
            if not self._quitting:
                self.quit()
            return
        event.ignore()
        self.hide()
        if not self._told_about_tray:
            self._told_about_tray = True
            self.notify(APP_NAME, "Still running in the tray. Right-click the icon to quit.")

    def quit(self) -> None:
        if self.busy:
            answer = QMessageBox.question(
                self, "Quit while busy?",
                f"{self.task.name} is still running. Quit anyway? An unfinished clip "
                "is not marked uploaded, so it is retried next run.",
            )
            if answer != QMessageBox.Yes:
                return
            self.stop.set()
            self.login_done.set()
        self._quitting = True
        self.tray.hide()
        QApplication.quit()


# ---------------------------------------------------------------------- main

def _hand_off_to_running_instance() -> bool:
    """If the app is already running, ask it to show itself and return True."""
    sock = QLocalSocket()
    sock.connectToServer(_INSTANCE_KEY)
    if sock.waitForConnected(300):
        sock.write(b"show")
        sock.flush()
        sock.waitForBytesWritten(300)
        sock.disconnectFromServer()
        return True
    return False


def _listen_for_other_instances(window: MainWindow) -> QLocalServer:
    QLocalServer.removeServer(_INSTANCE_KEY)  # clear a stale socket from a crash
    server = QLocalServer(window)
    server.newConnection.connect(window.show_window)
    server.listen(_INSTANCE_KEY)
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ytuploader")
    parser.add_argument("--config", default="config.toml", help="path to config.toml")
    parser.add_argument("--minimized", action="store_true", help="start hidden in the tray")
    args, qt_args = parser.parse_known_args(sys.argv[1:] if argv is None else argv)

    if sys.platform == "win32":
        # Give the taskbar our own icon/grouping instead of python.exe's.
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("ytuploader.app")

    app = QApplication([sys.argv[0], *qt_args])
    app.setApplicationName(APP_NAME)
    app.setWindowIcon(app_icon())
    app.setQuitOnLastWindowClosed(False)

    if _hand_off_to_running_instance():
        return 0

    if sys.platform == "win32":
        # Lets the installer/uninstaller detect a running app (Inno's AppMutex).
        _mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "ytuploader-app")  # noqa: F841

    window = MainWindow(args.config)
    _server = _listen_for_other_instances(window)  # noqa: F841 - keep alive
    if not args.minimized:
        window.show()
    QTimer.singleShot(0, window.first_run_check)

    code = app.exec()
    if window.task is not None and window.task.isRunning():
        # A browser upload can't be interrupted mid-file; don't let Qt tear the
        # thread down underneath it (that crashes). Exit the process directly.
        window.task.wait(3000)
        os._exit(code)
    return code


if __name__ == "__main__":
    sys.exit(main())
