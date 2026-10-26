"""The four tabs of the main window: Dashboard, Upload, Setup, Settings.

Pages only render state and raise signals; the main window owns the config,
the background task and the wiring between them.
"""

from __future__ import annotations

import copy
import html
import re
from datetime import datetime

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ..config import Config, config_from_dict, PROJECT_ROOT
from ..db import Database
from ..service import SetupState

_URL_RE = re.compile(r"https?://\S+")


def _heading(text: str) -> QLabel:
    label = QLabel(text)
    font = label.font()
    font.setPointSize(font.pointSize() + 4)
    font.setBold(True)
    label.setFont(font)
    return label


def _muted(text: str) -> QLabel:
    label = QLabel(text)
    label.setWordWrap(True)
    label.setStyleSheet("color: palette(placeholder-text);")
    return label


# ------------------------------------------------------------------ dashboard

class _StatCard(QFrame):
    def __init__(self, title: str):
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        layout = QVBoxLayout(self)
        self.value = QLabel("-")
        font = self.value.font()
        font.setPointSize(font.pointSize() + 12)
        font.setBold(True)
        self.value.setFont(font)
        layout.addWidget(self.value)
        layout.addWidget(_muted(title))


class DashboardPage(QWidget):
    refresh_requested = Signal()

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)

        top = QHBoxLayout()
        top.addWidget(_heading("Dashboard"))
        top.addStretch()
        self.updated = _muted("")
        top.addWidget(self.updated)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh_requested)
        top.addWidget(refresh)
        layout.addLayout(top)

        cards = QHBoxLayout()
        self.total = _StatCard("Clips tracked")
        self.uploaded = _StatCard("Uploaded")
        self.remaining = _StatCard("Waiting to upload")
        for card in (self.total, self.uploaded, self.remaining):
            cards.addWidget(card)
        layout.addLayout(cards)

        self.irregular = QLabel()
        self.irregular.setWordWrap(True)
        self.irregular.setStyleSheet("color: #C0392B;")
        self.irregular.hide()
        layout.addWidget(self.irregular)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Game / playlist", "Uploaded", "Remaining", "Total", "Done %"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for col in range(1, 5):
            self.table.horizontalHeader().setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setAlternatingRowColors(True)
        layout.addWidget(self.table, 1)

    def show_error(self, message: str) -> None:
        for card in (self.total, self.uploaded, self.remaining):
            card.value.setText("-")
        self.table.setRowCount(0)
        self.irregular.setText(message)
        self.irregular.show()

    def refresh(self, cfg: Config) -> None:
        try:
            db = Database(cfg.db_url)
            total, uploaded, remaining = db.counts()
            dist = db.distribution()
            irregular = db.irregular_videos()
        except Exception as e:  # noqa: BLE001
            self.show_error(f"Could not read the database at {cfg.db_path}: {e}")
            return

        self.total.value.setText(f"{total:,}")
        self.uploaded.value.setText(f"{uploaded:,}")
        self.remaining.value.setText(f"{remaining:,}")

        if irregular:
            self.irregular.setText(
                f"{len(irregular)} clips are marked uploaded but have no YouTube id "
                "(a half-finished run)."
            )
            self.irregular.show()
        else:
            self.irregular.hide()

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(dist))
        for row, d in enumerate(dist):
            pct = round(100 * d["uploaded"] / d["total"]) if d["total"] else 100
            values = [d["playlist"], d["uploaded"], d["remaining"], d["total"], pct]
            for col, value in enumerate(values):
                item = QTableWidgetItem()
                item.setData(Qt.DisplayRole, value)  # ints sort numerically
                if col:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if col == 2 and value:
                    item.setForeground(Qt.darkYellow)
                self.table.setItem(row, col, item)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(2, Qt.DescendingOrder)
        self.updated.setText(f"Updated {datetime.now():%H:%M}")


# --------------------------------------------------------------------- upload

class UploadPage(QWidget):
    scan_requested = Signal()
    upload_requested = Signal()
    run_requested = Signal()
    stop_requested = Signal()

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(_heading("Upload"))
        layout.addWidget(_muted(
            "Scan picks up new clips from your captures folder. Upload sends everything "
            "pending to YouTube and files it into the game's playlist. Stopping waits "
            "for the current clip to finish."
        ))

        buttons = QHBoxLayout()
        self.scan_btn = QPushButton("Scan for new clips")
        self.upload_btn = QPushButton("Upload pending")
        self.run_btn = QPushButton("Scan + upload")
        self.run_btn.setDefault(True)
        self.stop_btn = QPushButton("Stop")
        self.stop_btn.setEnabled(False)
        self.scan_btn.clicked.connect(self.scan_requested)
        self.upload_btn.clicked.connect(self.upload_requested)
        self.run_btn.clicked.connect(self.run_requested)
        self.stop_btn.clicked.connect(self.stop_requested)
        for b in (self.scan_btn, self.upload_btn, self.run_btn):
            buttons.addWidget(b)
        buttons.addStretch()
        buttons.addWidget(self.stop_btn)
        layout.addLayout(buttons)

        self.status = QLabel("Idle.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setTextVisible(True)
        self.progress.hide()
        layout.addWidget(self.progress)

        self.log = QTextBrowser()
        self.log.setOpenExternalLinks(True)
        self.log.setFont(QFont("Consolas", 9))
        layout.addWidget(self.log, 1)

        clear = QPushButton("Clear log")
        clear.clicked.connect(self.log.clear)
        row = QHBoxLayout()
        row.addStretch()
        row.addWidget(clear)
        layout.addLayout(row)

    def set_busy(self, busy: bool, label: str = "") -> None:
        for b in (self.scan_btn, self.upload_btn, self.run_btn):
            b.setEnabled(not busy)
        self.stop_btn.setEnabled(busy and label.startswith(("Upload", "Scan + upload")))
        self.stop_btn.setText("Stop")
        if busy:
            self.status.setText(f"{label}...")
        else:
            self.progress.hide()

    def stopping(self) -> None:
        self.stop_btn.setEnabled(False)
        self.stop_btn.setText("Stopping after this clip...")

    def append(self, text: str) -> None:
        stamp = datetime.now().strftime("%H:%M:%S")
        for line in text.split("\n"):
            escaped = html.escape(line)
            linked = _URL_RE.sub(lambda m: f'<a href="{m.group(0)}">{m.group(0)}</a>', escaped)
            self.log.append(
                f'<span style="color:gray">{stamp}</span>&nbsp;&nbsp;'
                f'<span style="white-space:pre">{linked}</span>'
            )

    def video_started(self, index: int, total: int, folder: str, name: str, size: str) -> None:
        self.status.setText(f"Uploading {index} of {total}: [{folder}] {name} ({size})")
        self.progress.setRange(0, max(total, 1))
        self.progress.setValue(index - 1)
        self.progress.setFormat(f"{index - 1} / {total} uploaded")
        self.progress.show()

    def video_done(self, _name: str, _url: str, count: int) -> None:
        self.progress.setValue(count)
        self.progress.setFormat(f"{count} / {self.progress.maximum()} uploaded")

    def finished(self, message: str) -> None:
        self.status.setText(message)


# ---------------------------------------------------------------------- setup

class _Step(QGroupBox):
    def __init__(self, title: str, description: str):
        super().__init__(title)
        self.grid = QGridLayout(self)
        self.state = QLabel()
        self.grid.addWidget(self.state, 0, 0, Qt.AlignTop)
        desc = _muted(description)
        self.grid.addWidget(desc, 0, 1)
        self.grid.setColumnStretch(1, 1)
        self.buttons = QHBoxLayout()
        self.grid.addLayout(self.buttons, 1, 1)

    def set_ok(self, ok: bool, text: str) -> None:
        mark, color = ("✓", "#27AE60") if ok else ("○", "#C0392B")
        self.state.setText(f'<span style="color:{color}; font-size:16pt">{mark}</span>')
        self.setToolTip(text)
        self.setTitle(f"{self.title().split('  -  ')[0]}  -  {text}")


class SetupPage(QWidget):
    login_requested = Signal()
    login_done = Signal()
    auth_requested = Signal()
    secrets_chosen = Signal(str)

    def __init__(self):
        super().__init__()
        self._secrets_ok = False
        layout = QVBoxLayout(self)
        layout.addWidget(_heading("One-time setup"))
        layout.addWidget(_muted(
            "Two things need doing once: log the uploader's own browser profile in to "
            "YouTube (for uploading), and authorise the YouTube API (for playlists). "
            "Both are remembered."
        ))

        self.login = _Step(
            "1. Log in to YouTube",
            "Opens a Chrome window using the uploader's private profile. Sign in to the "
            "channel you upload to, then click Done.",
        )
        self.login_btn = QPushButton("Open browser to log in")
        self.login_btn.clicked.connect(self.login_requested)
        self.login_done_btn = QPushButton("Done - I'm logged in")
        self.login_done_btn.clicked.connect(self.login_done)
        self.login_done_btn.hide()
        self.login.buttons.addWidget(self.login_btn)
        self.login.buttons.addWidget(self.login_done_btn)
        self.login.buttons.addStretch()
        layout.addWidget(self.login)

        self.secrets = _Step(
            "2. OAuth client file",
            "A Desktop-app OAuth client JSON from Google Cloud Console "
            "(APIs & Services > Credentials > Create OAuth client ID > Desktop app). "
            "A 'Web' client will not work.",
        )
        choose = QPushButton("Choose client_secrets.json...")
        choose.clicked.connect(self._choose_secrets)
        self.secrets.buttons.addWidget(choose)
        self.secrets.buttons.addStretch()
        layout.addWidget(self.secrets)

        self.auth = _Step(
            "3. Authorise the YouTube API",
            "Opens your normal browser to grant playlist access. Without this, clips "
            "still upload but aren't added to playlists.",
        )
        self.auth_btn = QPushButton("Authorise")
        self.auth_btn.clicked.connect(self.auth_requested)
        self.auth.buttons.addWidget(self.auth_btn)
        self.auth.buttons.addStretch()
        layout.addWidget(self.auth)

        folder = QHBoxLayout()
        folder.addWidget(QLabel(f"App folder: {PROJECT_ROOT}"))
        open_btn = QPushButton("Open folder")
        open_btn.clicked.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(PROJECT_ROOT)))
        )
        folder.addStretch()
        folder.addWidget(open_btn)
        layout.addLayout(folder)
        layout.addStretch()

    def _choose_secrets(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose OAuth client file", "", "JSON files (*.json)"
        )
        if path:
            self.secrets_chosen.emit(path)

    def refresh(self, state: SetupState | None) -> None:
        if state is None:
            for step in (self.login, self.secrets, self.auth):
                step.set_ok(False, "save Settings first")
            return
        self.login.set_ok(state.profile_exists,
                          "profile saved" if state.profile_exists else "not done")
        self.secrets.set_ok(state.client_secrets_exists,
                            "found" if state.client_secrets_exists else "missing")
        self.auth.set_ok(state.token_exists, "authorised" if state.token_exists else "not done")
        self.auth_btn.setText("Re-authorise" if state.token_exists else "Authorise")
        self._secrets_ok = state.client_secrets_exists
        self.auth_btn.setEnabled(self._secrets_ok)

    def set_busy(self, busy: bool, label: str = "") -> None:
        logging_in = busy and label == "Log in"
        self.login_btn.setVisible(not logging_in)
        self.login_btn.setEnabled(not busy)
        self.login_done_btn.setVisible(logging_in)
        self.auth_btn.setEnabled(not busy and self._secrets_ok)


# ------------------------------------------------------------------- settings

class _PathField(QWidget):
    def __init__(self, mode: str):
        super().__init__()
        self.mode = mode
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit()
        browse = QPushButton("Browse...")
        browse.clicked.connect(self._browse)
        row.addWidget(self.edit, 1)
        row.addWidget(browse)

    def _browse(self) -> None:
        start = self.edit.text() or str(PROJECT_ROOT)
        if self.mode == "dir":
            path = QFileDialog.getExistingDirectory(self, "Choose folder", start)
        else:
            path, _ = QFileDialog.getSaveFileName(
                self, "Choose file", start, options=QFileDialog.DontConfirmOverwrite
            )
        if path:
            self.edit.setText(path.replace("/", "\\"))

    def text(self) -> str:
        return self.edit.text().strip()

    def setText(self, text: str) -> None:
        self.edit.setText(text)


class SettingsPage(QWidget):
    saved = Signal(dict)

    def __init__(self):
        super().__init__()
        self._raw: dict = {}
        outer = QVBoxLayout(self)
        top = QHBoxLayout()
        top.addWidget(_heading("Settings"))
        top.addStretch()
        self.banner = QLabel()
        self.banner.setStyleSheet("color: #C0392B;")
        top.addWidget(self.banner)
        outer.addLayout(top)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        body = QWidget()
        form_layout = QVBoxLayout(body)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)

        # Paths
        self.captures = _PathField("dir")
        self.db_path = _PathField("file")
        self.profile = _PathField("dir")
        self.secrets = _PathField("file")
        self.token = _PathField("file")
        box = QGroupBox("Folders and files")
        f = QFormLayout(box)
        f.addRow("Captures folder", self.captures)
        f.addRow("Upload history DB", self.db_path)
        f.addRow("Browser profile", self.profile)
        f.addRow("OAuth client file", self.secrets)
        f.addRow("API token file", self.token)
        f.addRow("", _muted(f"Relative paths are relative to {PROJECT_ROOT}"))
        form_layout.addWidget(box)

        # Upload
        self.prefix = QLineEdit()
        self.visibility = QComboBox()
        self.visibility.addItems(["private", "unlisted", "public"])
        self.channel = QComboBox()
        self.channel.setEditable(True)
        self.channel.addItems(["chrome", "msedge"])
        self.headless = QCheckBox("Hide the browser window while uploading")
        self.cap = QSpinBox()
        self.cap.setRange(0, 10_000)
        self.cap.setSpecialValueText("No limit")
        self.order = QComboBox()
        self.order.addItem("Smallest backlog first", "fewest_remaining")
        self.order.addItem("By game name", "name")
        self.order.addItem("Random", "random")
        self.ignore = QLineEdit()
        box = QGroupBox("Uploading")
        f = QFormLayout(box)
        f.addRow("Playlist name prefix", self.prefix)
        f.addRow("Video visibility", self.visibility)
        f.addRow("Browser", self.channel)
        f.addRow("", self.headless)
        f.addRow("Max uploads per run", self.cap)
        f.addRow("Upload order", self.order)
        f.addRow("Skip folders (comma separated)", self.ignore)
        form_layout.addWidget(box)

        # Notify / power
        self.notify = QCheckBox("Email a summary when a run finishes")
        self.smtp_host = QLineEdit()
        self.smtp_port = QSpinBox()
        self.smtp_port.setRange(1, 65535)
        self.from_addr = QLineEdit()
        self.to_addr = QLineEdit()
        self.shutdown = QCheckBox("Shut down the PC when a run finishes")
        box = QGroupBox("When a run finishes")
        f = QFormLayout(box)
        f.addRow("", self.notify)
        f.addRow("SMTP host", self.smtp_host)
        f.addRow("SMTP port", self.smtp_port)
        f.addRow("From", self.from_addr)
        f.addRow("To", self.to_addr)
        f.addRow("", _muted("The Gmail app password goes in .env as GMAIL_APP_PASSWORD, "
                            "never here."))
        f.addRow("", self.shutdown)
        form_layout.addWidget(box)
        form_layout.addStretch()

        buttons = QHBoxLayout()
        buttons.addStretch()
        revert = QPushButton("Revert")
        revert.clicked.connect(lambda: self.load(self._raw, True))
        save = QPushButton("Save")
        save.setDefault(True)
        save.clicked.connect(self._save)
        buttons.addWidget(revert)
        buttons.addWidget(save)
        outer.addLayout(buttons)

    def load(self, raw: dict, exists: bool) -> None:
        self._raw = copy.deepcopy(raw)
        self.banner.setText("" if exists else "No config.toml yet - review and Save to create it.")
        paths = raw.get("paths", {})
        scan = raw.get("scan", {})
        upload = raw.get("upload", {})
        notify = raw.get("notify", {})
        power = raw.get("power", {})

        self.captures.setText(str(paths.get("captures_folder", "")))
        self.db_path.setText(str(paths.get("db_path", "")))
        self.profile.setText(str(paths.get("browser_profile_dir", "playwright_profile")))
        self.secrets.setText(str(paths.get("client_secrets_file", "client_secrets.json")))
        self.token.setText(str(paths.get("token_file", "token.json")))
        self.ignore.setText(", ".join(scan.get("ignore_folders", [])))

        self.prefix.setText(upload.get("playlist_prefix", "CAPTURES - "))
        self.visibility.setCurrentText(upload.get("visibility", "private"))
        self.channel.setCurrentText(upload.get("browser_channel", "chrome"))
        self.headless.setChecked(bool(upload.get("headless", True)))
        self.cap.setValue(int(upload.get("max_uploads_per_run", 0)))
        idx = self.order.findData(upload.get("order", "fewest_remaining"))
        self.order.setCurrentIndex(max(idx, 0))

        self.notify.setChecked(bool(notify.get("enabled", False)))
        self.smtp_host.setText(notify.get("smtp_host", "smtp.gmail.com"))
        self.smtp_port.setValue(int(notify.get("smtp_port", 587)))
        self.from_addr.setText(notify.get("from_addr", ""))
        self.to_addr.setText(notify.get("to_addr", ""))
        self.shutdown.setChecked(bool(power.get("shutdown_when_done", False)))

    def collect(self) -> dict:
        # Start from what was loaded so keys this form doesn't know survive a save.
        data = copy.deepcopy(self._raw)
        data.setdefault("paths", {}).update({
            "captures_folder": self.captures.text(),
            "db_path": self.db_path.text(),
            "browser_profile_dir": self.profile.text(),
            "client_secrets_file": self.secrets.text(),
            "token_file": self.token.text(),
        })
        data.setdefault("scan", {})["ignore_folders"] = [
            s.strip() for s in self.ignore.text().split(",") if s.strip()
        ]
        data.setdefault("upload", {}).update({
            "playlist_prefix": self.prefix.text(),
            "visibility": self.visibility.currentText(),
            "browser_channel": self.channel.currentText().strip(),
            "headless": self.headless.isChecked(),
            "max_uploads_per_run": self.cap.value(),
            "order": self.order.currentData(),
        })
        data.setdefault("notify", {}).update({
            "enabled": self.notify.isChecked(),
            "smtp_host": self.smtp_host.text().strip(),
            "smtp_port": self.smtp_port.value(),
            "from_addr": self.from_addr.text().strip(),
            "to_addr": self.to_addr.text().strip(),
        })
        data.setdefault("power", {})["shutdown_when_done"] = self.shutdown.isChecked()
        return data

    def _save(self) -> None:
        data = self.collect()
        try:
            cfg = config_from_dict(data)
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "Invalid settings", str(e))
            return
        if not cfg.captures_folder.exists():
            answer = QMessageBox.question(
                self, "Captures folder not found",
                f"{cfg.captures_folder} doesn't exist. Save anyway?",
            )
            if answer != QMessageBox.Yes:
                return
        self.saved.emit(data)
