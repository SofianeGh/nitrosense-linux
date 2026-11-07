"""Vector icons drawn with QPainter (no icon theme or image files needed)."""
from __future__ import annotations

import math

from .qt import QtCore, QtGui, Qt

QPointF = QtCore.QPointF
QRectF = QtCore.QRectF


def _pen(color: QtGui.QColor, width: float = 1.8) -> QtGui.QPen:
    pen = QtGui.QPen(color, width)
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    return pen


def _fan(p: QtGui.QPainter, cx: float, cy: float, r: float, color: QtGui.QColor) -> None:
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(color)
    for i in range(5):
        a = math.radians(i * 72)
        path = QtGui.QPainterPath()
        path.moveTo(cx, cy)
        tip = QPointF(cx + r * math.cos(a), cy + r * math.sin(a))
        side = QPointF(cx + r * 0.8 * math.cos(a + 0.9), cy + r * 0.8 * math.sin(a + 0.9))
        path.quadTo(QPointF(cx + r * 0.9 * math.cos(a - 0.35), cy + r * 0.9 * math.sin(a - 0.35)), tip)
        path.quadTo(side, QPointF(cx, cy))
        p.drawPath(path)
    p.setBrush(QtGui.QColor("#101010"))
    p.drawEllipse(QPointF(cx, cy), r * 0.22, r * 0.22)


def draw_icon(p: QtGui.QPainter, name: str, rect: QRectF, color: QtGui.QColor) -> None:
    """Draw icon `name` inside `rect` (24x24 design grid)."""
    p.save()
    size = min(rect.width(), rect.height())
    p.translate(rect.x() + (rect.width() - size) / 2, rect.y() + (rect.height() - size) / 2)
    p.scale(size / 24.0, size / 24.0)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
    p.setPen(_pen(color))
    p.setBrush(Qt.BrushStyle.NoBrush)

    if name == "quiet":
        p.drawRoundedRect(QRectF(2.5, 4.5, 19, 15), 4, 4)
        moon = QtGui.QPainterPath()
        moon.addEllipse(QPointF(11, 12), 4.6, 4.6)
        cut = QtGui.QPainterPath()
        cut.addEllipse(QPointF(13.4, 10.6), 3.9, 3.9)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        p.drawPath(moon.subtracted(cut))
        p.drawEllipse(QPointF(17, 8.5), 0.9, 0.9)
    elif name == "default":
        for dy in (0, 5):
            p.drawPolyline(QtGui.QPolygonF([QPointF(4, 6 + dy), QPointF(12, 13 + dy), QPointF(20, 6 + dy)]))
        p.drawLine(QPointF(8, 4), QPointF(16, 4))
    elif name == "performance":
        p.drawArc(QRectF(3, 5, 18, 18), 200 * 16, -220 * 16)
        p.drawLine(QPointF(12, 14), QPointF(17, 8.5))
        p.setBrush(color)
        p.drawEllipse(QPointF(12, 14), 1.6, 1.6)
        p.drawLine(QPointF(2, 19), QPointF(6, 19))
    elif name in ("fan_auto", "fan_max", "fan_custom"):
        _fan(p, 10, 11, 8, color)
        p.setPen(_pen(color, 1.6))
        p.setBrush(Qt.BrushStyle.NoBrush)
        if name == "fan_auto":
            f = QtGui.QFont()
            f.setPixelSize(10)
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRectF(14, 13, 10, 11), Qt.AlignmentFlag.AlignCenter, "A")
        elif name == "fan_max":
            for y in (15, 18, 21):
                p.drawLine(QPointF(17, y), QPointF(23, y))
        else:
            p.drawLine(QPointF(15, 19), QPointF(23, 19))
            p.setBrush(color)
            p.drawEllipse(QPointF(19, 19), 1.8, 1.8)
    elif name == "keyboard":
        p.drawRoundedRect(QRectF(2, 6, 20, 13), 2, 2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(color)
        for row, y in enumerate((9, 12.5)):
            for i in range(6):
                p.drawRect(QRectF(4.2 + i * 2.8 + (row * 1.2), y, 1.6, 1.6))
        p.drawRect(QRectF(7, 15.8, 10, 1.6))
    elif name == "gear":
        p.setBrush(color)
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(8):
            p.save()
            p.translate(12, 12)
            p.rotate(i * 45)
            p.drawRoundedRect(QRectF(-1.9, -10.5, 3.8, 5), 1, 1)
            p.restore()
        ring = QtGui.QPainterPath()
        ring.addEllipse(QPointF(12, 12), 7, 7)
        hole = QtGui.QPainterPath()
        hole.addEllipse(QPointF(12, 12), 3, 3)
        p.drawPath(ring.subtracted(hole))
    elif name == "minimize":
        p.setPen(_pen(color, 2.4))
        p.drawLine(QPointF(5, 17), QPointF(19, 17))
    elif name == "close":
        p.setPen(_pen(color, 2.4))
        p.drawLine(QPointF(6, 6), QPointF(18, 18))
        p.drawLine(QPointF(18, 6), QPointF(6, 18))
    elif name in ("eye_small", "eye_big"):
        scale = 0.7 if name == "eye_small" else 1.0
        p.translate(12, 12)
        p.scale(scale, scale)
        eye = QtGui.QPainterPath()
        eye.moveTo(-9, 0)
        eye.quadTo(0, -8, 9, 0)
        eye.quadTo(0, 8, -9, 0)
        p.drawPath(eye)
        p.setBrush(color)
        p.drawEllipse(QPointF(0, 0), 2.6, 2.6)
        if name == "eye_big":
            for a in (-60, -90, -120):
                r = math.radians(a)
                p.drawLine(QPointF(10 * math.cos(r), 10 * math.sin(r)), QPointF(12.5 * math.cos(r), 12.5 * math.sin(r)))
    elif name in ("arrow_left", "arrow_right"):
        sign = 1 if name == "arrow_right" else -1
        p.translate(12, 12)
        p.scale(sign, 1)
        p.setPen(_pen(color, 2.2))
        for x in (-9, -6, -3):
            p.drawLine(QPointF(x, 0), QPointF(x + 1, 0))
        p.drawLine(QPointF(0, 0), QPointF(8, 0))
        p.drawPolyline(QtGui.QPolygonF([QPointF(4, -4), QPointF(8, 0), QPointF(4, 4)]))
    p.restore()


def app_icon() -> QtGui.QIcon:
    icon = QtGui.QIcon()
    for size in (32, 64, 128, 256):
        pixmap = QtGui.QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        p = QtGui.QPainter(pixmap)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor("#141414"))
        p.drawRoundedRect(QRectF(0, 0, size, size), size * 0.2, size * 0.2)
        ring = QtGui.QPen(QtGui.QColor("#E60000"), size * 0.06)
        p.setPen(ring)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(size / 2, size / 2), size * 0.36, size * 0.36)
        _fan(p, size / 2, size / 2, size * 0.28, QtGui.QColor("#E8E8E8"))
        p.end()
        icon.addPixmap(pixmap)
    return icon
