"""The app icon, drawn in code so there's no binary asset to keep in sync.

`save_ico` writes the same artwork to an .ico for the PyInstaller build.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap, QPolygonF

_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _draw(size: int, busy: bool = False) -> QPixmap:
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    s = size / 64.0

    # Red rounded "play button" body (amber while an upload is running).
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#E8A317") if busy else QColor("#E62117"))
    p.drawRoundedRect(QRectF(2 * s, 10 * s, 60 * s, 44 * s), 12 * s, 12 * s)

    # White upload arrow.
    p.setBrush(QColor("white"))
    p.drawPolygon(QPolygonF([
        QPointF(32 * s, 17 * s),
        QPointF(46 * s, 32 * s),
        QPointF(37 * s, 32 * s),
        QPointF(37 * s, 46 * s),
        QPointF(27 * s, 46 * s),
        QPointF(27 * s, 32 * s),
        QPointF(18 * s, 32 * s),
    ]))
    p.end()
    return pm


def app_icon(busy: bool = False) -> QIcon:
    icon = QIcon()
    for size in _SIZES:
        icon.addPixmap(_draw(size, busy))
    return icon


def save_ico(path: str) -> None:
    from PySide6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication([])  # noqa: F841 - needed for QPixmap
    if not _draw(256).save(path, "ICO"):
        raise RuntimeError(f"could not write {path} (Qt ICO plugin missing?)")


if __name__ == "__main__":
    import sys

    save_ico(sys.argv[1] if len(sys.argv) > 1 else "icon.ico")
