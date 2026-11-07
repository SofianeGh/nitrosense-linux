"""Settings popup: units, keyboard timeout, battery limit, GPU info, diagnostics."""
from __future__ import annotations

import json

from .. import __version__
from . import theme
from .qt import QtGui, QtWidgets, Qt
from .widgets import DialogFrame, IconButton

BACKEND_LABELS = {"sysfs": "noyau (sysfs)", "hwmon": "noyau (hwmon)", "wmi": "WMI via acpi_call",
                  "none": "indisponible"}


def describe_caps(status: dict) -> str:
    caps = status.get("caps", {})
    transport = caps.get("transport", {})
    lines = [
        f"NitroSense Linux {status.get('version', __version__)}",
        f"Modèle : {caps.get('model')}  —  BIOS {caps.get('bios')}  —  noyau {caps.get('kernel')}",
        f"Transport WMI : {transport.get('name')} ({'disponible' if transport.get('available') else 'absent'})",
    ]
    for alias, path in (transport.get("methods") or {}).items():
        lines.append(f"  {alias:8s} {path}")
    for key, label in (("profile", "Modes"), ("fan_rpm", "Régime ventilos"), ("fan_control", "Contrôle ventilos"),
                       ("keyboard", "Clavier RGB"), ("backlight_timeout", "Délai clavier"),
                       ("battery_limit", "Limite batterie")):
        lines.append(f"{label:18s}: {BACKEND_LABELS.get(caps.get(key), caps.get(key))}")
    lines.append(f"Choix platform_profile : {' '.join(caps.get('profile_choices') or []) or '—'}")
    if not caps.get("model_ok"):
        lines.append("ATTENTION : modèle différent de l'AN515-58, écritures bloquées par défaut.")
    return "\n".join(lines)


class SettingsDialog(QtWidgets.QDialog):
    def __init__(self, main):
        super().__init__(main)
        self.main = main
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("Paramètres")
        self.resize(720, 640)
        status = main.status or {}
        caps = status.get("caps", {})

        frame = DialogFrame(self)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(frame)
        layout = QtWidgets.QVBoxLayout(frame)
        layout.setContentsMargins(34, 22, 26, 24)
        layout.setSpacing(10)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("Paramètres")
        title.setFont(theme.font(15))
        header.addWidget(title)
        header.addStretch(1)
        close = IconButton("close", "Fermer", 30)
        close.clicked.connect(self.accept)
        header.addWidget(close)
        layout.addLayout(header)

        layout.addWidget(self._section("Paramètres avancés"))
        units = QtWidgets.QHBoxLayout()
        units.addWidget(QtWidgets.QLabel("Unités de température"))
        units.addStretch(1)
        self.celsius = QtWidgets.QRadioButton("°C")
        self.fahrenheit = QtWidgets.QRadioButton("°F")
        unit = main.prefs.get("temp_unit", "C")
        self.celsius.setChecked(unit == "C")
        self.fahrenheit.setChecked(unit == "F")
        self.celsius.toggled.connect(self._on_unit)
        units.addWidget(self.celsius)
        units.addWidget(self.fahrenheit)
        layout.addLayout(units)

        layout.addWidget(self._section("Paramètres du clavier"))
        self.backlight = QtWidgets.QCheckBox("Rétroéclairage éteint après 30 secondes")
        self.backlight.setChecked(bool(status.get("backlight_timeout")))
        self.backlight.setEnabled(caps.get("backlight_timeout", "none") != "none")
        self.backlight.clicked.connect(
            lambda checked: main.call("set_backlight_timeout", tag="settings", enabled=checked))
        layout.addWidget(self.backlight)

        layout.addWidget(self._section("Batterie"))
        self.battery = QtWidgets.QCheckBox("Limiter la charge à 80 % (prolonge la durée de vie de la batterie)")
        self.battery.setChecked(bool(status.get("battery_limit")))
        self.battery.setEnabled(caps.get("battery_limit", "none") != "none")
        self.battery.clicked.connect(
            lambda checked: main.call("set_battery_limit", tag="settings", enabled=checked))
        layout.addWidget(self.battery)
        note = QtWidgets.QLabel("Équivalent du « mode santé de la batterie » d'Acer Care Center.")
        note.setObjectName("dim")
        layout.addWidget(note)

        layout.addWidget(self._section("GPU"))
        mux = status.get("gpu_mux") or {}
        mode = {"hybrid": "hybride (Optimus)", "discrete": "GPU dédié seulement"}.get(mux.get("mode"), "inconnu")
        gpu = QtWidgets.QLabel(f"Mode GPU (commutateur MUX) : {mode}.\n"
                               "À changer depuis NitroSense sous Windows (redémarrage requis) : "
                               "volontairement non modifiable ici.")
        gpu.setObjectName("dim")
        gpu.setWordWrap(True)
        layout.addWidget(gpu)

        layout.addWidget(self._section("Diagnostic"))
        self.diag = QtWidgets.QPlainTextEdit(describe_caps(status))
        self.diag.setReadOnly(True)
        layout.addWidget(self.diag, 1)
        buttons = QtWidgets.QHBoxLayout()
        for text, handler in (("Réappliquer les réglages", lambda: main.call("reapply", tag="settings")),
                              ("Redétecter le matériel", lambda: main.call("redetect", tag="settings")),
                              ("Copier", self._copy)):
            button = QtWidgets.QPushButton(text)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)

    @staticmethod
    def _section(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setObjectName("section")
        return label

    def _on_unit(self):
        unit = "C" if self.celsius.isChecked() else "F"
        self.main.prefs.set("temp_unit", unit)
        self.main.refresh_units()

    def _copy(self):
        text = self.diag.toPlainText() + "\n\n" + json.dumps((self.main.status or {}).get("caps", {}), indent=2)
        QtGui.QGuiApplication.clipboard().setText(text)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.accept()
        else:
            super().keyPressEvent(event)
