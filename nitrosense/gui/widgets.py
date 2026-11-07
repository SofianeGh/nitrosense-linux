"""Custom-painted widgets reproducing the NitroSense 3.1 look."""
from __future__ import annotations

from collections import deque

from . import theme
from .icons import draw_icon
from .qt import QtCore, QtGui, QtWidgets, Qt, Signal

QPointF = QtCore.QPointF
QRectF = QtCore.QRectF


def chamfer_path(rect: QRectF, cut: float) -> QtGui.QPainterPath:
    x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
    path = QtGui.QPainterPath()
    path.moveTo(x + cut, y)
    path.lineTo(x + w - cut, y)
    path.lineTo(x + w, y + cut)
    path.lineTo(x + w, y + h - cut)
    path.lineTo(x + w - cut, y + h)
    path.lineTo(x + cut, y + h)
    path.lineTo(x, y + h - cut)
    path.lineTo(x, y + cut)
    path.closeSubpath()
    return path


def to_display_temp(celsius, unit: str):
    if celsius is None:
        return None
    return celsius * 9 / 5 + 32 if unit == "F" else celsius


class CachedBackground(QtWidgets.QWidget):
    """Renders its background once per size (children animate on top of it)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache = None

    def resizeEvent(self, event):
        self._cache = None
        super().resizeEvent(event)

    def invalidate(self):
        self._cache = None
        self.update()

    def render_background(self, p: QtGui.QPainter, rect: QRectF) -> None:
        raise NotImplementedError

    def paintEvent(self, event):
        ratio = self.devicePixelRatioF()
        if self._cache is None or self._cache.width() != int(self.width() * ratio):
            pixmap = QtGui.QPixmap(max(1, int(self.width() * ratio)), max(1, int(self.height() * ratio)))
            pixmap.setDevicePixelRatio(ratio)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QtGui.QPainter(pixmap)
            painter.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
            self.render_background(painter, QRectF(0, 0, self.width(), self.height()))
            painter.end()
            self._cache = pixmap
        p = QtGui.QPainter(self)
        p.drawPixmap(0, 0, self._cache)


class Backdrop(CachedBackground):
    """Window background: dark frame with the diagonal NitroSense texture."""

    def render_background(self, p, rect):
        p.fillRect(rect, theme.color(theme.BG))
        frame = chamfer_path(rect.adjusted(6, 6, -6, -6), 22)
        gradient = QtGui.QLinearGradient(0, 0, 0, rect.height())
        gradient.setColorAt(0, QtGui.QColor("#1E1E1E"))
        gradient.setColorAt(1, QtGui.QColor("#131313"))
        p.fillPath(frame, gradient)
        p.save()
        p.setClipPath(frame)
        p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255, 9), 1))
        height = int(rect.height())
        for x in range(-height, int(rect.width()), 5):
            p.drawLine(x, 0, x + height, height)
        p.restore()
        p.setPen(QtGui.QPen(QtGui.QColor("#303030"), 1.5))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawPath(frame)


class Panel(CachedBackground):
    """Panel with the slanted grey header tab ("Ctrl ventilo", "Mode"...)."""

    HEADER = 30

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.setContentsMargins(18, self.HEADER + 14, 18, 14)

    def render_background(self, p, rect):
        body = chamfer_path(QRectF(0.5, self.HEADER / 2, rect.width() - 1, rect.height() - self.HEADER / 2 - 0.5), 12)
        p.fillPath(body, QtGui.QColor(18, 18, 18, 235))
        p.setPen(QtGui.QPen(theme.color(theme.PANEL_BORDER), 1))
        p.drawPath(body)
        f = theme.font(11, "demibold")
        width = QtGui.QFontMetricsF(f).horizontalAdvance(self.title) + 40
        tab = QtGui.QPainterPath()
        tab.moveTo(0, 7)
        tab.lineTo(7, 0)
        tab.lineTo(width, 0)
        tab.lineTo(width + 26, self.HEADER)
        tab.lineTo(0, self.HEADER)
        tab.closeSubpath()
        gradient = QtGui.QLinearGradient(0, 0, 0, self.HEADER)
        gradient.setColorAt(0, QtGui.QColor("#626262"))
        gradient.setColorAt(1, QtGui.QColor("#2F2F2F"))
        p.fillPath(tab, gradient)
        p.setPen(QtGui.QPen(QtGui.QColor("#8A8A8A"), 1))
        p.drawLine(QPointF(7, 0.5), QPointF(width, 0.5))
        p.setPen(theme.color(theme.TEXT))
        p.setFont(f)
        p.drawText(QRectF(14, 0, width, self.HEADER), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self.title)


class DialogFrame(CachedBackground):
    """Background of the lighting / settings popups."""

    def render_background(self, p, rect):
        body = chamfer_path(rect.adjusted(1, 1, -1, -1), 16)
        gradient = QtGui.QLinearGradient(0, 0, 0, rect.height())
        gradient.setColorAt(0, QtGui.QColor("#242424"))
        gradient.setColorAt(1, QtGui.QColor("#171717"))
        p.fillPath(body, gradient)
        p.setPen(QtGui.QPen(QtGui.QColor("#5A5A5A"), 1.2))
        p.drawPath(body)
        p.setPen(QtGui.QPen(QtGui.QColor("#9A9A9A"), 3))
        p.drawLine(QPointF(rect.width() * 0.35, 1.5), QPointF(rect.width() * 0.65, 1.5))


class FanGauge(QtWidgets.QWidget):
    """Big fan with the RPM in the middle; the blades spin with the real speed."""

    BLADES = 26

    def __init__(self, label: str, label_side: str = "left", parent=None):
        super().__init__(parent)
        self.label = label
        self.label_side = label_side
        self.rpm = None
        self.angle = 0.0
        self._blade = None
        self._blade_radius = 0.0
        self.setMinimumSize(280, 170)
        self.timer = QtCore.QTimer(self)
        self.timer.timeout.connect(self._step)
        self.timer.start(40)

    def set_rpm(self, rpm) -> None:
        if rpm != self.rpm:
            self.rpm = rpm
            self.update()

    def _step(self):
        if self.rpm and self.isVisible():
            self.angle = (self.angle + self.rpm * 0.0042) % 360
            self.update()

    def _gauge_rect(self) -> QRectF:
        size = min(self.height() - 4, self.width() - 90)
        label_space = 70
        if self.label_side == "left":
            x = label_space + (self.width() - label_space - size) / 2
        else:
            x = (self.width() - label_space - size) / 2
        return QRectF(x, (self.height() - size) / 2, size, size)

    def _blade_path(self, radius: float) -> QtGui.QPainterPath:
        if self._blade is None or abs(self._blade_radius - radius) > 0.5:
            r0, r1 = radius * 0.66, radius * 0.99
            path = QtGui.QPainterPath()
            path.moveTo(QPointF(-radius * 0.02, -r0))
            path.cubicTo(QPointF(radius * 0.09, -r0 - radius * 0.07), QPointF(radius * 0.21, -r1 + radius * 0.06),
                         QPointF(radius * 0.17, -r1))
            path.cubicTo(QPointF(radius * 0.12, -r1 + radius * 0.05), QPointF(radius * 0.03, -r0 - radius * 0.05),
                         QPointF(-radius * 0.06, -r0))
            path.closeSubpath()
            self._blade, self._blade_radius = path, radius
        return self._blade

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        g = self._gauge_rect()
        c = g.center()
        radius = g.width() / 2
        active = bool(self.rpm)

        p.setPen(theme.color(theme.TEXT))
        p.setFont(theme.font(15))
        if self.label_side == "left":
            p.drawText(QRectF(0, c.y() - 20, g.left() - 12, 40),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, self.label)
        else:
            p.drawText(QRectF(g.right() + 12, c.y() - 20, self.width() - g.right() - 12, 40),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.label)

        gradient = QtGui.QRadialGradient(QPointF(0, 0), radius)
        gradient.setColorAt(0.6, QtGui.QColor("#4A4A4A" if active else "#2C2C2C"))
        gradient.setColorAt(1.0, QtGui.QColor("#D2D2D2" if active else "#4E4E4E"))
        blade = self._blade_path(radius)
        p.save()
        p.translate(c)
        p.rotate(self.angle)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QBrush(gradient))
        for _ in range(self.BLADES):
            p.drawPath(blade)
            p.rotate(360 / self.BLADES)
        p.restore()

        inner = radius * 0.62
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor("#121212"))
        p.drawEllipse(c, inner, inner)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for width, alpha in ((8, 35), (4, 90), (1.6, 230)):
            p.setPen(QtGui.QPen(QtGui.QColor(230, 0, 0, alpha if active else alpha // 3), width))
            p.drawEllipse(c, inner, inner)

        p.setPen(theme.color(theme.TEXT))
        p.setFont(theme.font(max(12.0, radius * 0.2), "bold"))
        text = f"{self.rpm}" if self.rpm is not None else "—"
        p.drawText(QRectF(c.x() - inner, c.y() - inner * 0.55, inner * 2, inner * 0.8),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, text)
        p.setPen(theme.color(theme.TEXT_DIM))
        p.setFont(theme.font(8.5))
        p.drawText(QRectF(c.x() - inner, c.y() + inner * 0.3, inner * 2, inner * 0.4),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, "RPM")


class HoverButton(QtWidgets.QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._hover = False
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def enterEvent(self, event):
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event):
        self._hover = False
        self.update()
        super().leaveEvent(event)


class ChoiceButton(HoverButton):
    """Icon + label entry of the "Mode" and "Ctrl ventilo" lists."""

    def __init__(self, text: str, icon: str, parent=None, separator: bool = True):
        super().__init__(parent)
        self.setText(text)
        self.icon_name = icon
        self.separator = separator
        self.setCheckable(True)
        self.setMinimumHeight(50)
        self.setSizePolicy(QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Fixed)

    def sizeHint(self):
        return QtCore.QSize(220, 54)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        if not self.isEnabled():
            color = theme.color(theme.TEXT_DISABLED)
        elif self.isChecked():
            color = theme.color(theme.RED)
        elif self._hover:
            color = QtGui.QColor("#C8C8C8")
        else:
            color = theme.color(theme.TEXT_OFF)
        h = self.height()
        draw_icon(p, self.icon_name, QRectF(6, (h - 30) / 2, 30, 30), color)
        p.setPen(color)
        p.setFont(theme.font(14))
        p.drawText(QRectF(50, 0, self.width() - 50, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                   self.text())
        if self.separator:
            p.setPen(QtGui.QPen(QtGui.QColor("#2A2A2A"), 1))
            p.drawLine(QPointF(0, h - 0.5), QPointF(self.width(), h - 0.5))


class HexRadio(HoverButton):
    """Radio button with the hexagonal NitroSense indicator."""

    def __init__(self, text: str, parent=None):
        super().__init__(parent)
        self.setText(text)
        self.setCheckable(True)
        self.setMinimumHeight(30)

    def sizeHint(self):
        width = QtGui.QFontMetrics(theme.font(11)).horizontalAdvance(self.text()) + 44
        return QtCore.QSize(width, 30)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        cy = self.height() / 2
        hexagon = QtGui.QPolygonF([QPointF(4, cy), QPointF(10, cy - 8), QPointF(22, cy - 8),
                                   QPointF(28, cy), QPointF(22, cy + 8), QPointF(10, cy + 8)])
        p.setPen(QtGui.QPen(QtGui.QColor("#5A5A5A"), 1))
        p.setBrush(QtGui.QColor("#3A3A3A"))
        p.drawPolygon(hexagon)
        if self.isChecked():
            inner = QtGui.QPolygonF([QPointF(8, cy), QPointF(12, cy - 5), QPointF(20, cy - 5),
                                     QPointF(24, cy), QPointF(20, cy + 5), QPointF(12, cy + 5)])
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(theme.color(theme.RED))
            p.drawPolygon(inner)
        if not self.isEnabled():
            color = theme.color(theme.TEXT_OFF)
        elif self._hover or self.isChecked():
            color = theme.color(theme.TEXT)
        else:
            color = QtGui.QColor("#BDBDBD")
        p.setPen(color)
        p.setFont(theme.font(11))
        p.drawText(QRectF(36, 0, self.width() - 36, self.height()),
                   Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())


class IconButton(HoverButton):
    def __init__(self, icon: str, tooltip: str = "", size: int = 34, parent=None):
        super().__init__(parent)
        self.icon_name = icon
        self.setToolTip(tooltip)
        self.setFixedSize(size, size)

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        if not self.isEnabled():
            color = theme.color(theme.TEXT_DISABLED)
        elif self.isChecked() or (self._hover and self.icon_name == "close"):
            color = theme.color(theme.RED)
        elif self._hover:
            color = QtGui.QColor("#FFFFFF")
        else:
            color = QtGui.QColor("#BDBDBD")
        draw_icon(p, self.icon_name, QRectF(4, 4, self.width() - 8, self.height() - 8), color)


class Swatch(HoverButton):
    """Colour square (basic / recent colours, zone colour buttons)."""

    def __init__(self, color: str | None = None, size: int = 24, parent=None):
        super().__init__(parent)
        self.color = color
        self.setFixedSize(size, size)
        if color:
            self.setToolTip(color)

    def set_color(self, color: str | None) -> None:
        self.color = color
        self.setToolTip(color or "")
        self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        rect = QRectF(1.5, 1.5, self.width() - 3, self.height() - 3)
        if self.color:
            fill = QtGui.QColor(self.color)
            if not self.isEnabled():
                fill = fill.darker(300)
            p.fillRect(rect, fill)
        border = theme.RED if (self.isChecked() or self._hover) and self.isEnabled() else "#4A4A4A"
        p.setPen(QtGui.QPen(QtGui.QColor(border), 2 if self.isChecked() else 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(rect)


class ValueBox(QtWidgets.QWidget):
    """"CPU 3700 MHz" box of the Mode panel."""

    def __init__(self, title: str, unit: str, parent=None):
        super().__init__(parent)
        self.title = title
        self.unit = unit
        self.value = None
        self.setMinimumSize(110, 84)

    def set_value(self, value) -> None:
        if value != self.value:
            self.value = value
            self.update()

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        p.setPen(theme.color(theme.TEXT_DIM))
        p.setFont(theme.font(11))
        p.drawText(QRectF(0, 0, self.width(), 20), Qt.AlignmentFlag.AlignCenter, self.title)
        box = QRectF(2, 24, self.width() - 4, self.height() - 26)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor("#1C1C1C"))
        p.drawRoundedRect(box, 8, 8)
        p.setPen(theme.color(theme.TEXT))
        p.setFont(theme.font(17, "bold"))
        text = f"{int(self.value)}" if self.value is not None else "—"
        p.drawText(QRectF(box.x(), box.y() + 2, box.width(), box.height() * 0.62),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignBottom, text)
        p.setPen(theme.color(theme.TEXT_DIM))
        p.setFont(theme.font(9))
        p.drawText(QRectF(box.x(), box.y() + box.height() * 0.64, box.width(), box.height() * 0.34),
                   Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, self.unit)


class HistoryGraph(QtWidgets.QWidget):
    """Temperature (red) and load (orange) history, like the NitroSense graphs."""

    def __init__(self, title: str, length: int = 150, parent=None):
        super().__init__(parent)
        self.title = title
        self.samples: deque = deque(maxlen=length)
        self.t_min = None
        self.t_max = None
        self.inactive = None
        self.unit = "C"
        self.setMinimumHeight(118)

    def set_unit(self, unit: str) -> None:
        self.unit = unit
        self.update()

    def set_inactive(self, text) -> None:
        if text != self.inactive:
            self.inactive = text
            self.update()

    def push(self, temp, load) -> None:
        self.samples.append((temp, load))
        if temp is not None:
            self.t_min = temp if self.t_min is None else min(self.t_min, temp)
            self.t_max = temp if self.t_max is None else max(self.t_max, temp)
        self.update()

    def _deg(self, value) -> str:
        shown = to_display_temp(value, self.unit)
        return "—" if shown is None else f"{shown:.0f}°"

    def _series_path(self, plot: QRectF, index: int):
        step = plot.width() / max(1, self.samples.maxlen - 1)
        offset = self.samples.maxlen - len(self.samples)
        points = []
        for i, sample in enumerate(self.samples):
            value = sample[index]
            if value is None:
                continue
            x = plot.left() + (offset + i) * step
            y = plot.bottom() - max(0.0, min(100.0, value)) / 100.0 * plot.height()
            points.append(QPointF(x, y))
        if len(points) < 2:
            return None, None
        line = QtGui.QPainterPath(points[0])
        for point in points[1:]:
            line.lineTo(point)
        area = QtGui.QPainterPath(line)
        area.lineTo(points[-1].x(), plot.bottom())
        area.lineTo(points[0].x(), plot.bottom())
        area.closeSubpath()
        return line, area

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        right = 116
        plot = QRectF(0, 18, self.width() - right, self.height() - 20)

        p.setPen(theme.color(theme.TEXT_DIM))
        p.setFont(theme.font(9))
        p.drawText(QRectF(0, 0, plot.right(), 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   f"Min : {self._deg(self.t_min)}      Max : {self._deg(self.t_max)}")

        background = QtGui.QLinearGradient(0, plot.top(), 0, plot.bottom())
        background.setColorAt(0, QtGui.QColor("#240707"))
        background.setColorAt(1, QtGui.QColor("#0E0303"))
        p.fillRect(plot, background)
        p.setPen(QtGui.QPen(QtGui.QColor(110, 20, 20, 70), 1))
        x = plot.left()
        while x < plot.right():
            p.drawLine(QPointF(x, plot.top()), QPointF(x, plot.bottom()))
            x += 12
        for frac in (0.25, 0.5, 0.75):
            y = plot.top() + plot.height() * frac
            p.drawLine(QPointF(plot.left(), y), QPointF(plot.right(), y))

        p.save()
        p.setClipRect(plot)
        for index, color, fill_alpha in ((1, theme.ORANGE, 70), (0, theme.RED_BRIGHT, 80)):
            line, area = self._series_path(plot, index)
            if line is None:
                continue
            fill = QtGui.QColor(color)
            fill.setAlpha(fill_alpha)
            p.fillPath(area, fill)
            p.setPen(QtGui.QPen(QtGui.QColor(color), 1.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawPath(line)
        p.restore()

        p.setPen(theme.color(theme.TEXT))
        p.setFont(theme.font(11))
        p.drawText(QRectF(plot.left() + 10, plot.top() + 6, 120, 22),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, self.title)

        last_temp = next((s[0] for s in reversed(self.samples) if s[0] is not None), None)
        last_load = next((s[1] for s in reversed(self.samples) if s[1] is not None), None)
        column = QRectF(plot.right() + 14, plot.top(), right - 14, plot.height())
        p.setPen(QtGui.QColor(theme.RED_BRIGHT))
        p.setFont(theme.font(21))
        p.drawText(QRectF(column.x(), column.y(), column.width(), column.height() / 2),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom, self._deg(last_temp))
        p.setPen(QtGui.QColor(theme.ORANGE))
        load_text = "—" if last_load is None else f"{last_load:.0f}"
        p.drawText(QRectF(column.x(), column.y() + column.height() / 2, column.width(), column.height() / 2),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, load_text)
        metrics = QtGui.QFontMetricsF(theme.font(21))
        p.setFont(theme.font(11))
        p.drawText(QRectF(column.x() + metrics.horizontalAdvance(load_text) + 6,
                          column.y() + column.height() / 2 + 8, 30, 24),
                   Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop, "%")

        if self.inactive:
            p.fillRect(plot, QtGui.QColor(0, 0, 0, 150))
            p.setPen(theme.color(theme.TEXT_DIM))
            p.setFont(theme.font(12))
            p.drawText(plot, Qt.AlignmentFlag.AlignCenter, self.inactive)


class Overlay(QtWidgets.QLabel):
    """Red notice drawn over a disabled area ("Contrôle du ventilateur désactivé...")."""

    def __init__(self, text: str, parent: QtWidgets.QWidget):
        super().__init__(text, parent)
        self.setWordWrap(True)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setStyleSheet(f"background: rgba(12,12,12,215); color: {theme.RED_BRIGHT}; padding: 12px;"
                           "font-size: 10.5pt;")
        parent.installEventFilter(self)
        self.hide()

    def eventFilter(self, obj, event):
        if obj is self.parent() and event.type() == QtCore.QEvent.Type.Resize:
            self.setGeometry(obj.rect())
        return False


class TitleBar(QtWidgets.QWidget):
    """Draggable top bar for the frameless window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._drag = None
        self.setFixedHeight(64)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            handle = self.window().windowHandle()
            if handle is not None and handle.startSystemMove():
                return
            self._drag = event.globalPosition().toPoint() - self.window().frameGeometry().topLeft()

    def mouseMoveEvent(self, event):
        if self._drag is not None:
            self.window().move(event.globalPosition().toPoint() - self._drag)

    def mouseReleaseEvent(self, event):
        self._drag = None

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        nitro = theme.font(19, "bold", 7)
        sense = theme.font(19, "light", 7)
        w1 = QtGui.QFontMetricsF(nitro).horizontalAdvance("NITRO")
        w2 = QtGui.QFontMetricsF(sense).horizontalAdvance("SENSE")
        x = (self.width() - w1 - w2) / 2
        p.setFont(nitro)
        p.setPen(QtGui.QColor("#D6D6D6"))
        p.drawText(QRectF(x, 6, w1 + 2, 36), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "NITRO")
        p.setFont(sense)
        p.setPen(QtGui.QColor("#9C9C9C"))
        p.drawText(QRectF(x + w1, 6, w2 + 4, 36), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, "SENSE")
        p.setFont(theme.font(8, "demibold", 4))
        p.setPen(theme.color(theme.RED))
        p.drawText(QRectF(0, 40, self.width(), 16), Qt.AlignmentFlag.AlignHCenter, "LINUX")
