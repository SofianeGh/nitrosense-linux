"""Main window: "Ctrl ventilo", "Mode" and "Surveillance" panels."""
from __future__ import annotations

import os
import time
from pathlib import Path

from .. import APP_NAME
from ..client import ClientError
from ..controller import DEFAULT_KEYBOARD
from ..paths import load_json, save_json_atomic
from . import theme
from .icons import app_icon
from .lighting import LightingDialog
from .qt import QtCore, QtGui, QtWidgets, Qt, Signal
from .settings import SettingsDialog
from .widgets import (Backdrop, ChoiceButton, FanGauge, HistoryGraph, IconButton, Overlay, Panel, TitleBar,
                      ValueBox)

FAN_MODES = [("auto", "Auto", "fan_auto"), ("max", "Max", "fan_max"), ("custom", "Perso", "fan_custom")]
PROFILES = [("quiet", "Silencieux", "quiet"), ("default", "Par défaut", "default"),
            ("performance", "Performance", "performance")]
HOLD_SECONDS = 2.5


class Prefs:
    """GUI-only preferences (~/.config/nitrosense-linux/gui.json)."""

    def __init__(self, path: Path | None = None):
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
        self.path = path or base / "nitrosense-linux" / "gui.json"
        self.data = {"temp_unit": "C", "recent_colors": []}
        self.data.update(load_json(self.path, {}))

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value) -> None:
        self.data[key] = value
        try:
            save_json_atomic(self.path, self.data)
        except OSError:
            pass


class Bridge(QtCore.QObject):
    result = Signal(str, object)


class Job(QtCore.QRunnable):
    def __init__(self, fn, tag: str, bridge: Bridge):
        super().__init__()
        self.fn, self.tag, self.bridge = fn, tag, bridge

    def run(self):
        try:
            response = self.fn()
        except ClientError as exc:
            response = {"ok": False, "error": str(exc), "unreachable": True}
        except Exception as exc:  # noqa: BLE001 - shown in the status line
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.bridge.result.emit(self.tag, response)


class FanModeBox(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout_ = QtWidgets.QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(0)
        self.overlay = Overlay("Contrôle du ventilateur désactivé en mode Silencieux.", self)


class MainWindow(QtWidgets.QWidget):
    def __init__(self, client, prefs: Prefs | None = None, native_frame: bool = False, demo: bool = False):
        super().__init__()
        self.client = client
        self.prefs = prefs or Prefs()
        self.demo = demo
        self.status: dict | None = None
        self._hold: dict[str, float] = {}
        self._polling = False
        self._flash_until = 0.0
        self.bridge = Bridge()
        self.bridge.result.connect(self._on_result)
        self.pool = QtCore.QThreadPool.globalInstance()
        if not native_frame:
            self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle(APP_NAME + (" (démo)" if demo else ""))
        self.setWindowIcon(app_icon())
        self.resize(1280, 780)
        self.setMinimumSize(1120, 720)
        self._build()

        self.poll_timer = QtCore.QTimer(self)
        self.poll_timer.setInterval(1000)
        self.poll_timer.timeout.connect(self.poll)
        self.poll_timer.start()
        self.speed_timer = QtCore.QTimer(self)
        self.speed_timer.setSingleShot(True)
        self.speed_timer.setInterval(350)
        self.speed_timer.timeout.connect(self._send_speeds)
        QtGui.QShortcut(QtGui.QKeySequence("Ctrl+Q"), self, activated=self.close)
        self.poll()

    # ------------------------------------------------------------------ build
    def _build(self):
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        backdrop = Backdrop(self)
        outer.addWidget(backdrop)
        root = QtWidgets.QVBoxLayout(backdrop)
        root.setContentsMargins(30, 10, 30, 14)
        root.setSpacing(12)
        root.addWidget(self._build_title())
        root.addWidget(self._build_fan_panel(), 5)
        bottom = QtWidgets.QHBoxLayout()
        bottom.setSpacing(16)
        bottom.addWidget(self._build_mode_panel())
        bottom.addWidget(self._build_monitor_panel(), 1)
        root.addLayout(bottom, 6)
        self.status_label = QtWidgets.QLabel("Connexion au service…")
        self.status_label.setObjectName("status")
        root.addWidget(self.status_label)

    def _build_title(self) -> QtWidgets.QWidget:
        bar = TitleBar()
        layout = QtWidgets.QHBoxLayout(bar)
        layout.setContentsMargins(10, 0, 0, 0)
        self.model_label = QtWidgets.QLabel("Nitro")
        self.model_label.setFont(theme.font(12, "demibold", 1))
        self.model_label.setStyleSheet("color: #BDBDBD;")
        layout.addWidget(self.model_label)
        layout.addStretch(1)
        self.keyboard_button = IconButton("keyboard", "Éclairage du clavier")
        self.keyboard_button.clicked.connect(self.open_lighting)
        settings = IconButton("gear", "Paramètres")
        settings.clicked.connect(self.open_settings)
        minimize = IconButton("minimize", "Réduire")
        minimize.clicked.connect(self.showMinimized)
        close = IconButton("close", "Fermer")
        close.clicked.connect(self.close)
        for button in (self.keyboard_button, settings, minimize, close):
            layout.addWidget(button)
            layout.addSpacing(6)
        return bar

    def _build_fan_panel(self) -> QtWidgets.QWidget:
        panel = Panel("Ctrl ventilo")
        layout = QtWidgets.QHBoxLayout(panel)
        layout.setSpacing(18)
        self.fan_box = FanModeBox()
        self.fan_box.setFixedWidth(250)
        self.fan_buttons = {}
        group = QtWidgets.QButtonGroup(self)
        for key, text, icon in FAN_MODES:
            button = ChoiceButton(text, icon)
            button.clicked.connect(lambda _=False, k=key: self.on_fan_mode(k))
            group.addButton(button)
            self.fan_box.layout_.addWidget(button)
            self.fan_buttons[key] = button
        self.fan_box.layout_.addStretch(1)
        layout.addWidget(self.fan_box)

        self.gauges, self.sliders, self.slider_labels, self.auto_checks, self.fan_controls = {}, {}, {}, {}, {}
        for key, label, side in (("cpu", "CPU", "left"), ("gpu", "GPU", "right")):
            column = QtWidgets.QVBoxLayout()
            column.setSpacing(4)
            gauge = FanGauge(label, side)
            column.addWidget(gauge, 1)
            controls = QtWidgets.QWidget()
            row = QtWidgets.QHBoxLayout(controls)
            row.setContentsMargins(40, 0, 40, 0)
            minus = QtWidgets.QLabel("–")
            minus.setObjectName("dim")
            slider = QtWidgets.QSlider(Qt.Orientation.Horizontal)
            slider.setRange(0, 100)
            slider.setSingleStep(5)
            slider.setPageStep(10)
            slider.valueChanged.connect(lambda value, k=key: self.on_slider(k, value))
            plus = QtWidgets.QLabel("+")
            plus.setObjectName("dim")
            value_label = QtWidgets.QLabel("50 %")
            value_label.setFixedWidth(46)
            auto = QtWidgets.QCheckBox("Auto")
            auto.clicked.connect(lambda _=False, k=key: self.on_fan_auto(k))
            for widget in (minus, slider, plus, value_label, auto):
                row.addWidget(widget, 1 if widget is slider else 0)
            policy = controls.sizePolicy()
            policy.setRetainSizeWhenHidden(True)
            controls.setSizePolicy(policy)
            controls.hide()
            column.addWidget(controls)
            layout.addLayout(column, 1)
            self.gauges[key], self.sliders[key] = gauge, slider
            self.slider_labels[key], self.auto_checks[key], self.fan_controls[key] = value_label, auto, controls
        return panel

    def _build_mode_panel(self) -> QtWidgets.QWidget:
        panel = Panel("Mode")
        panel.setFixedWidth(300)
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setSpacing(4)
        boxes = QtWidgets.QHBoxLayout()
        self.cpu_box = ValueBox("CPU", "MHz")
        self.gpu_box = ValueBox("GPU", "MHz")
        boxes.addWidget(self.cpu_box)
        boxes.addWidget(self.gpu_box)
        layout.addLayout(boxes)
        layout.addSpacing(8)
        self.mode_buttons = {}
        group = QtWidgets.QButtonGroup(self)
        for index, (key, text, icon) in enumerate(PROFILES):
            button = ChoiceButton(text, icon, separator=index < len(PROFILES) - 1)
            button.clicked.connect(lambda _=False, k=key: self.on_profile(k))
            group.addButton(button)
            layout.addWidget(button)
            self.mode_buttons[key] = button
        self.mode_note = QtWidgets.QLabel("")
        self.mode_note.setObjectName("warning")
        self.mode_note.setWordWrap(True)
        layout.addWidget(self.mode_note)
        layout.addStretch(1)
        return panel

    def _build_monitor_panel(self) -> QtWidgets.QWidget:
        panel = Panel("Surveillance")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setSpacing(8)
        self.unit_label = QtWidgets.QLabel()
        self.unit_label.setObjectName("dim")
        layout.addWidget(self.unit_label)
        self.cpu_graph = HistoryGraph("CPU")
        self.gpu_graph = HistoryGraph("GPU")
        layout.addWidget(self.cpu_graph, 1)
        layout.addWidget(self.gpu_graph, 1)
        self.refresh_units()
        return panel

    # ------------------------------------------------------------- plumbing
    def call(self, cmd: str, tag: str | None = None, **params) -> None:
        self.pool.start(Job(lambda: self.client.request(cmd, **params), tag or cmd, self.bridge))

    def poll(self, force: bool = False) -> None:
        if self._polling and not force:
            return
        self._polling = True
        self.call("status", tag="status")

    def _held(self, group: str) -> bool:
        return time.monotonic() < self._hold.get(group, 0.0)

    def _hold_group(self, group: str) -> None:
        self._hold[group] = time.monotonic() + HOLD_SECONDS

    def flash(self, text: str, error: bool = False) -> None:
        self.status_label.setObjectName("statusError" if error else "status")
        self.status_label.setStyle(self.status_label.style())
        self.status_label.setText(text)
        self._flash_until = time.monotonic() + 6

    def _on_result(self, tag: str, response: dict) -> None:
        if tag == "status":
            self._polling = False
            if response.get("ok"):
                self.apply_status(response)
            else:
                self._set_offline(response.get("error", "erreur inconnue"))
            return
        if not response.get("ok"):
            self._hold.clear()
            self.flash(response.get("error") or "Erreur", error=True)
        elif tag == "reapply" and response.get("errors"):
            self.flash(" ; ".join(response["errors"]), error=True)
        elif tag in ("settings", "reapply", "redetect"):
            self.flash("Réglage appliqué.")
        self.poll(force=True)

    def _set_offline(self, error: str) -> None:
        self.flash(f"{error} — lancer : sudo systemctl start nitrosensed", error=True)
        for button in list(self.fan_buttons.values()) + list(self.mode_buttons.values()):
            button.setEnabled(False)

    # ----------------------------------------------------------- status → UI
    def apply_status(self, st: dict) -> None:
        self.status = st
        caps = st.get("caps", {})
        self.model_label.setText(caps.get("model") or "Nitro")

        fan = st.get("fan", {})
        fan_ok = caps.get("fan_control", "none") != "none"
        locked = bool(st.get("fan_control_locked"))
        rpm = st.get("rpm") or {}
        for key, gauge in self.gauges.items():
            gauge.set_rpm(rpm.get(key))
        for button in self.fan_buttons.values():
            button.setEnabled(fan_ok and not locked)
        self.fan_box.overlay.setVisible(locked and fan_ok)
        self.fan_box.setToolTip("" if fan_ok else
                                "Contrôle des ventilateurs indisponible : charger le module acpi_call (voir README).")
        if not self._held("fan"):
            for key, button in self.fan_buttons.items():
                button.setChecked(key == fan.get("mode"))
            for key in ("cpu", "gpu"):
                if not self.sliders[key].isSliderDown():
                    self.sliders[key].blockSignals(True)
                    self.sliders[key].setValue(int(fan.get(key, 50)))
                    self.sliders[key].blockSignals(False)
                    self.slider_labels[key].setText(f"{int(fan.get(key, 50))} %")
                self.auto_checks[key].setChecked(bool(fan.get(f"{key}_auto")))
                self.sliders[key].setEnabled(not fan.get(f"{key}_auto"))
            self._show_custom(fan.get("mode") == "custom" and fan_ok and not locked)

        profiles = caps.get("profiles", [])
        blocked = st.get("performance_blocked")
        for key, button in self.mode_buttons.items():
            button.setEnabled(key in profiles and not (key == "performance" and blocked))
        if not self._held("profile"):
            for key, button in self.mode_buttons.items():
                button.setChecked(key == st.get("profile"))
        self.mode_note.setText(blocked or ("" if profiles else "Changement de mode indisponible (voir Paramètres)."))

        cpu = st.get("cpu", {})
        gpu = st.get("gpu", {})
        self.cpu_box.set_value(cpu.get("mhz"))
        self.gpu_box.set_value(gpu.get("mhz") if gpu.get("state") == "active" else None)
        self.cpu_graph.push(cpu.get("temp"), cpu.get("load"))
        if gpu.get("state") == "suspended":
            self.gpu_graph.set_inactive("GPU distinct inactif.")
            self.gpu_graph.push(None, None)
        elif gpu.get("state") == "absent":
            self.gpu_graph.set_inactive("GPU NVIDIA non détecté.")
        else:
            self.gpu_graph.set_inactive(None)
            self.gpu_graph.push(gpu.get("temp"), gpu.get("load"))

        self.keyboard_button.setToolTip("Éclairage du clavier" if caps.get("keyboard") != "none" else
                                        "Éclairage du clavier (indisponible : module acpi_call absent)")
        if time.monotonic() > self._flash_until:
            self.status_label.setObjectName("status")
            self.status_label.setStyle(self.status_label.style())
            if st.get("fan_guard"):
                text = "Sécurité thermique : température élevée, ventilateurs repassés en Auto."
            else:
                text = (f"Connecté au service {st.get('version', '')}"
                        f"{' — MODE DÉMO (matériel simulé)' if self.demo else ''}"
                        f" — modes : {caps.get('profile')}, ventilateurs : {caps.get('fan_control')},"
                        f" clavier : {caps.get('keyboard')}")
            self.status_label.setText(text)

    def refresh_units(self) -> None:
        unit = self.prefs.get("temp_unit", "C")
        self.unit_label.setText(f"Température (°{unit}) / Chargement (%)")
        self.cpu_graph.set_unit(unit)
        self.gpu_graph.set_unit(unit)

    def _show_custom(self, visible: bool) -> None:
        for controls in self.fan_controls.values():
            controls.setVisible(visible)

    # -------------------------------------------------------------- actions
    def _fan_params(self) -> dict:
        return {"cpu": self.sliders["cpu"].value(), "gpu": self.sliders["gpu"].value(),
                "cpu_auto": self.auto_checks["cpu"].isChecked(), "gpu_auto": self.auto_checks["gpu"].isChecked()}

    def on_profile(self, key: str) -> None:
        self._hold_group("profile")
        for k, button in self.mode_buttons.items():
            button.setChecked(k == key)
        if key == "quiet":
            self._hold_group("fan")
            self._show_custom(False)
        self.call("set_profile", name=key)

    def on_fan_mode(self, key: str) -> None:
        self._hold_group("fan")
        self._show_custom(key == "custom")
        params = {"mode": key}
        if key == "custom":
            params.update(self._fan_params())
        self.call("set_fan", **params)

    def on_slider(self, key: str, value: int) -> None:
        self.slider_labels[key].setText(f"{value} %")
        self._hold_group("fan")
        self.speed_timer.start()

    def _send_speeds(self) -> None:
        self._hold_group("fan")
        self.call("set_fan_speed", **self._fan_params())

    def on_fan_auto(self, key: str) -> None:
        self._hold_group("fan")
        self.sliders[key].setEnabled(not self.auto_checks[key].isChecked())
        self.call("set_fan", mode="custom", **self._fan_params())

    def open_lighting(self, modal: bool = True) -> LightingDialog:
        status = self.status or {}
        enabled = status.get("caps", {}).get("keyboard", "none") != "none"
        keyboard = status.get("keyboard") or DEFAULT_KEYBOARD
        dialog = LightingDialog(self, keyboard, enabled=enabled and bool(status))
        self._center(dialog)
        if modal:
            dialog.exec()
        else:
            dialog.show()
        return dialog

    def open_settings(self, modal: bool = True) -> SettingsDialog:
        dialog = SettingsDialog(self)
        self._center(dialog)
        if modal:
            dialog.exec()
        else:
            dialog.show()
        return dialog

    def closeEvent(self, event):
        self.poll_timer.stop()
        self.speed_timer.stop()
        for gauge in self.gauges.values():
            gauge.timer.stop()
        self.pool.waitForDone(1500)
        super().closeEvent(event)

    def _center(self, dialog: QtWidgets.QWidget) -> None:
        geometry = dialog.frameGeometry()
        geometry.moveCenter(self.frameGeometry().center())
        dialog.move(geometry.topLeft())
