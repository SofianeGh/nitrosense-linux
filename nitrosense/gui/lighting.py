"""Keyboard lighting popup ("Clavier Éclairage"): static 4 zones or effects."""
from __future__ import annotations

import copy

from . import theme
from .qt import QtCore, QtGui, QtWidgets, Qt, Signal
from .widgets import DialogFrame, HexRadio, IconButton, Swatch

QRectF = QtCore.QRectF

EFFECTS = [("breathing", "Respiration"), ("wave", "Onde"), ("zoom", "Zoom"),
           ("shifting", "Déplacement"), ("neon", "Néon")]
BRIGHTNESS_STEPS = (0, 25, 50, 75, 100)


class KeyboardPreview(QtWidgets.QWidget):
    """Laptop keyboard drawn in 4 sections; click a section to select it."""

    zoneClicked = Signal(int)
    ROWS = (14, 14, 13, 12, 11, 8)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.zones = [{"on": True, "color": "#FF0000"} for _ in range(4)]
        self.brightness = 100
        self.selected = 0
        self.setMinimumSize(520, 170)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def _board(self) -> QRectF:
        return QRectF(8, 8, self.width() - 16, self.height() - 16)

    def mousePressEvent(self, event):
        board = self._board()
        x = event.position().x() - board.left()
        if 0 <= x <= board.width():
            self.zoneClicked.emit(min(3, int(x / (board.width() / 4))))

    def paintEvent(self, event):
        p = QtGui.QPainter(self)
        p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing)
        board = self._board()
        p.setPen(QtGui.QPen(QtGui.QColor("#3C3C3C"), 1.5))
        p.setBrush(QtGui.QColor("#101010"))
        p.drawRoundedRect(board, 10, 10)
        inner = board.adjusted(12, 12, -12, -12)
        row_h = inner.height() / len(self.ROWS)
        zone_w = inner.width() / 4
        for row, count in enumerate(self.ROWS):
            key_w = inner.width() / count
            for k in range(count):
                key = QRectF(inner.left() + k * key_w + 2, inner.top() + row * row_h + 2, key_w - 4, row_h - 4)
                if row == len(self.ROWS) - 1 and k == 3:
                    key = QRectF(key.left(), key.top(), key_w * 3 - 4, key.height())
                elif row == len(self.ROWS) - 1 and k in (4, 5):
                    continue
                zone = min(3, int((key.center().x() - inner.left()) / zone_w))
                info = self.zones[zone]
                if info["on"] and self.brightness > 0:
                    glow = QtGui.QColor(info["color"])
                    glow.setAlphaF(0.25 + 0.75 * self.brightness / 100)
                else:
                    glow = QtGui.QColor("#262626")
                p.setPen(QtGui.QPen(glow, 1.4))
                p.setBrush(QtGui.QColor("#1A1A1A"))
                p.drawRoundedRect(key, 3, 3)
        section = QRectF(inner.left() + self.selected * zone_w, inner.top() - 6, zone_w, inner.height() + 12)
        p.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF"), 1.2, Qt.PenStyle.DashLine))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(section.adjusted(1, 1, -1, -1))


class LightingDialog(QtWidgets.QDialog):
    def __init__(self, main, keyboard: dict, enabled: bool = True):
        super().__init__(main)
        self.main = main
        self.kb = copy.deepcopy(keyboard)
        if self.kb.get("mode") == "off":
            self.kb["mode"] = "static"
            self.kb["brightness"] = 0
        self.selected_zone = 0
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("Clavier Éclairage")
        self.resize(960, 600)
        self.apply_timer = QtCore.QTimer(self)
        self.apply_timer.setSingleShot(True)
        self.apply_timer.setInterval(220)
        self.apply_timer.timeout.connect(self._apply)
        self._build()
        self._load()
        if not enabled:
            for widget in (self.static_radio, self.dynamic_radio, self.brightness, self.stack,
                           self.more_button, *self.base_swatches, *self.recent_swatches):
                widget.setEnabled(False)
            self.notice.setText("Éclairage indisponible : le module acpi_call n'est pas chargé (voir README).")

    # ----------------------------------------------------------------- build
    def _build(self):
        frame = DialogFrame(self)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(34, 22, 26, 24)
        layout.setSpacing(12)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Clavier  Éclairage")
        title.setFont(theme.font(15))
        header.addWidget(title)
        header.addStretch(1)
        self.close_button = IconButton("close", "Fermer", 30)
        self.close_button.clicked.connect(self.accept)
        header.addWidget(self.close_button)
        layout.addLayout(header)

        mode_row = QtWidgets.QHBoxLayout()
        self.static_radio = HexRadio("Statique")
        self.dynamic_radio = HexRadio("Dynamique")
        group = QtWidgets.QButtonGroup(self)
        for radio in (self.static_radio, self.dynamic_radio):
            group.addButton(radio)
            mode_row.addWidget(radio)
        self.static_radio.clicked.connect(lambda: self._set_mode("static"))
        self.dynamic_radio.clicked.connect(lambda: self._set_mode("effect"))
        mode_row.addStretch(1)
        mode_row.addWidget(IconButton("eye_small", "Luminosité", 26))
        self.brightness = QtWidgets.QSlider(Qt.Orientation.Horizontal)
        self.brightness.setRange(0, len(BRIGHTNESS_STEPS) - 1)
        self.brightness.setPageStep(1)
        self.brightness.setFixedWidth(160)
        self.brightness.setToolTip("Luminosité (tout à gauche : éteint)")
        self.brightness.valueChanged.connect(self._on_brightness)
        mode_row.addWidget(self.brightness)
        mode_row.addWidget(IconButton("eye_big", "Luminosité", 26))
        layout.addLayout(mode_row)

        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self._build_static_page())
        self.stack.addWidget(self._build_dynamic_page())
        layout.addWidget(self.stack, 1)

        colors = QtWidgets.QGridLayout()
        colors.setHorizontalSpacing(8)
        colors.setVerticalSpacing(6)
        base_label = QtWidgets.QLabel("Couleurs de base")
        base_label.setObjectName("dim")
        recent_label = QtWidgets.QLabel("Couleurs récentes")
        recent_label.setObjectName("dim")
        colors.addWidget(base_label, 0, 0)
        colors.addWidget(recent_label, 0, 2)
        base_row = QtWidgets.QHBoxLayout()
        base_row.setSpacing(4)
        self.base_swatches = []
        for name, value in theme.BASE_COLORS:
            swatch = Swatch(value, 26)
            swatch.setToolTip(name)
            swatch.clicked.connect(lambda _=False, v=value: self.pick_color(v))
            base_row.addWidget(swatch)
            self.base_swatches.append(swatch)
        colors.addLayout(base_row, 1, 0)
        colors.setColumnMinimumWidth(1, 40)
        recent_row = QtWidgets.QHBoxLayout()
        recent_row.setSpacing(4)
        self.recent_swatches = []
        for _ in range(9):
            swatch = Swatch(None, 26)
            swatch.clicked.connect(lambda _=False, s=swatch: s.color and self.pick_color(s.color))
            recent_row.addWidget(swatch)
            self.recent_swatches.append(swatch)
        colors.addLayout(recent_row, 1, 2)
        self.more_button = QtWidgets.QPushButton("Plus de couleur...")
        self.more_button.clicked.connect(self._more_colors)
        colors.addWidget(self.more_button, 1, 4)
        colors.setColumnStretch(3, 1)
        layout.addLayout(colors)

        self.notice = QtWidgets.QLabel("")
        self.notice.setObjectName("warning")
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)

    def _build_static_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 6, 0, 0)
        self.preview = KeyboardPreview()
        self.preview.zoneClicked.connect(self._select_zone)
        layout.addWidget(self.preview, 1)
        zones = QtWidgets.QHBoxLayout()
        self.zone_checks = []
        self.zone_buttons = []
        for index in range(4):
            box = QtWidgets.QHBoxLayout()
            check = QtWidgets.QCheckBox(f"Section {index + 1}")
            check.setToolTip("Allumer / éteindre cette zone")
            check.clicked.connect(lambda checked, i=index: self._toggle_zone(i, checked))
            button = Swatch("#FF0000", 30)
            button.setCheckable(True)
            button.setToolTip("Choisir la couleur de cette zone")
            button.clicked.connect(lambda _=False, i=index: self._select_zone(i))
            box.addWidget(check)
            box.addWidget(button)
            box.addStretch(1)
            zones.addLayout(box, 1)
            self.zone_checks.append(check)
            self.zone_buttons.append(button)
        layout.addLayout(zones)
        self.all_zones = QtWidgets.QCheckBox("Appliquer la couleur aux 4 zones")
        layout.addWidget(self.all_zones)
        return page

    def _build_dynamic_page(self) -> QtWidgets.QWidget:
        page = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        label = QtWidgets.QLabel("Effets lumière")
        label.setObjectName("dim")
        layout.addWidget(label)
        grid = QtWidgets.QGridLayout()
        grid.setHorizontalSpacing(80)
        self.effect_radios = {}
        group = QtWidgets.QButtonGroup(self)
        for i, (key, text) in enumerate(EFFECTS):
            radio = HexRadio(text)
            radio.clicked.connect(lambda _=False, k=key: self._set_effect(k))
            group.addButton(radio)
            grid.addWidget(radio, i % 3, i // 3)
            self.effect_radios[key] = radio
        grid.setColumnStretch(2, 1)
        layout.addLayout(grid)
        layout.addSpacing(16)

        row = QtWidgets.QHBoxLayout()
        speed_box = QtWidgets.QVBoxLayout()
        speed_label = QtWidgets.QLabel("Vitesse")
        speed_label.setObjectName("dim")
        speed_box.addWidget(speed_label)
        self.speed = QtWidgets.QSlider(Qt.Orientation.Horizontal)
        self.speed.setRange(1, 9)
        self.speed.setPageStep(1)
        self.speed.setFixedWidth(220)
        self.speed.valueChanged.connect(self._on_speed)
        speed_box.addWidget(self.speed)
        row.addLayout(speed_box)
        row.addSpacing(60)
        direction_box = QtWidgets.QVBoxLayout()
        direction_label = QtWidgets.QLabel("Direction")
        direction_label.setObjectName("dim")
        direction_box.addWidget(direction_label)
        arrows = QtWidgets.QHBoxLayout()
        self.direction_buttons = {}
        group = QtWidgets.QButtonGroup(self)
        for key, icon, tip in (("left", "arrow_left", "Vers la gauche"), ("right", "arrow_right", "Vers la droite")):
            button = IconButton(icon, tip, 40)
            button.setCheckable(True)
            button.clicked.connect(lambda _=False, k=key: self._set_direction(k))
            group.addButton(button)
            arrows.addWidget(button)
            self.direction_buttons[key] = button
        arrows.addStretch(1)
        direction_box.addLayout(arrows)
        speed_box.addStretch(1)
        direction_box.addStretch(1)
        row.addLayout(direction_box)
        row.addStretch(1)
        row.setAlignment(Qt.AlignmentFlag.AlignTop)
        layout.addLayout(row)
        layout.addStretch(1)
        return page

    # ------------------------------------------------------------------ state
    def _load(self):
        kb = self.kb
        level = min(range(len(BRIGHTNESS_STEPS)), key=lambda i: abs(BRIGHTNESS_STEPS[i] - kb.get("brightness", 100)))
        self.brightness.blockSignals(True)
        self.brightness.setValue(level)
        self.brightness.blockSignals(False)
        static = kb.get("mode", "static") == "static"
        self.static_radio.setChecked(static)
        self.dynamic_radio.setChecked(not static)
        self.stack.setCurrentIndex(0 if static else 1)
        for index, zone in enumerate(kb["zones"]):
            self.zone_checks[index].setChecked(zone["on"])
            self.zone_buttons[index].set_color(zone["color"])
        self.effect_radios.get(kb.get("effect", "wave"), self.effect_radios["wave"]).setChecked(True)
        self.speed.blockSignals(True)
        self.speed.setValue(int(kb.get("speed", 5)))
        self.speed.blockSignals(False)
        self.direction_buttons[kb.get("direction", "right")].setChecked(True)
        self._refresh()

    def _refresh(self):
        for index, button in enumerate(self.zone_buttons):
            button.setChecked(index == self.selected_zone)
        self.preview.zones = self.kb["zones"]
        self.preview.selected = self.selected_zone
        self.preview.brightness = self.kb["brightness"]
        self.preview.update()
        effect = self.kb.get("effect", "wave")
        dynamic = self.kb["mode"] == "effect"
        uses_color = not dynamic or effect != "neon"
        for swatch in self.base_swatches + self.recent_swatches:
            swatch.setEnabled(uses_color)
        self.more_button.setEnabled(uses_color)
        for button in self.direction_buttons.values():
            button.setEnabled(effect in ("wave", "shifting"))
        recent = self.main.prefs.get("recent_colors", [])
        for swatch, value in zip(self.recent_swatches, recent + [None] * 9):
            swatch.set_color(value)

    def _schedule(self):
        self._refresh()
        self.apply_timer.start()

    def _apply(self):
        self.main.call("set_keyboard", tag="keyboard", keyboard=copy.deepcopy(self.kb))

    # --------------------------------------------------------------- actions
    def _set_mode(self, mode: str):
        self.kb["mode"] = mode
        self.stack.setCurrentIndex(0 if mode == "static" else 1)
        self._schedule()

    def _on_brightness(self, level: int):
        self.kb["brightness"] = BRIGHTNESS_STEPS[level]
        self._schedule()

    def _select_zone(self, index: int):
        self.selected_zone = index
        self._refresh()

    def _toggle_zone(self, index: int, checked: bool):
        self.kb["zones"][index]["on"] = checked
        self._schedule()

    def _set_effect(self, key: str):
        self.kb["effect"] = key
        self._schedule()

    def _on_speed(self, value: int):
        self.kb["speed"] = value
        self._schedule()

    def _set_direction(self, key: str):
        self.kb["direction"] = key
        self._schedule()

    def pick_color(self, value: str):
        value = QtGui.QColor(value).name().upper()
        if self.kb["mode"] == "static":
            targets = range(4) if self.all_zones.isChecked() else [self.selected_zone]
            for index in targets:
                self.kb["zones"][index]["color"] = value
                self.kb["zones"][index]["on"] = True
                self.zone_buttons[index].set_color(value)
                self.zone_checks[index].setChecked(True)
        else:
            self.kb["color"] = value
        recent = [c for c in self.main.prefs.get("recent_colors", []) if c != value]
        self.main.prefs.set("recent_colors", ([value] + recent)[:9])
        self._schedule()

    def _more_colors(self):
        if self.kb["mode"] == "static":
            current = self.kb["zones"][self.selected_zone]["color"]
        else:
            current = self.kb.get("color", "#FFFFFF")
        chosen = QtWidgets.QColorDialog.getColor(QtGui.QColor(current), self, "Choisir une couleur")
        if chosen.isValid():
            self.pick_color(chosen.name())

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.accept()
        else:
            super().keyPressEvent(event)
