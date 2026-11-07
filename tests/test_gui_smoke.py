"""Drives the real GUI against the simulated laptop (skipped without PyQt6/PySide6).

Set NITROSENSE_SHOTS=/some/dir to also save screenshots of every step.
"""
import os
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

try:
    from nitrosense.gui.qt import QtWidgets
except ImportError:  # pragma: no cover
    QtWidgets = None


@unittest.skipIf(QtWidgets is None, "PyQt6 / PySide6 non installé")
class GuiSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        os.environ["XDG_CONFIG_HOME"] = tempfile.mkdtemp(prefix="nitrosense-gui-")
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        from nitrosense.gui import theme
        cls.app.setStyleSheet(theme.STYLESHEET)

    def setUp(self):
        from nitrosense import wmi_codes as W
        from nitrosense.client import LocalClient
        from nitrosense.demo import make_demo_controller
        from nitrosense.gui.app import MainWindow, Prefs
        self.W = W
        self.controller, self.world = make_demo_controller()
        self.fw = self.controller.hw.transport
        self.win = MainWindow(LocalClient(self.controller), Prefs(), demo=True)
        self.win.show()
        self.wait_for(lambda: self.win.status is not None)
        self.shots = os.environ.get("NITROSENSE_SHOTS")

    def tearDown(self):
        self.win.close()

    def wait(self, seconds):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            time.sleep(0.02)

    def wait_for(self, predicate, timeout=6.0):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(0.02)
        self.fail("condition jamais atteinte")

    def shot(self, widget, name):
        if self.shots:
            os.makedirs(self.shots, exist_ok=True)
            widget.grab().save(os.path.join(self.shots, name))

    def test_main_window_reflects_status(self):
        self.wait(2.5)
        self.assertTrue(self.win.mode_buttons["default"].isChecked())
        self.assertTrue(self.win.fan_buttons["auto"].isChecked())
        self.assertIsNotNone(self.win.gauges["cpu"].rpm)
        self.assertGreater(len(self.win.cpu_graph.samples), 1)
        self.shot(self.win, "01-main.png")

    def test_custom_fans(self):
        W = self.W
        self.win.fan_buttons["custom"].click()
        self.wait_for(lambda: self.controller.state["fan"]["mode"] == "custom")
        self.assertEqual(self.fw.fan_modes, {"cpu": W.FAN_MODE_CUSTOM, "gpu": W.FAN_MODE_CUSTOM})
        self.assertTrue(self.win.fan_controls["cpu"].isVisible())
        self.win.sliders["cpu"].setValue(80)
        self.wait_for(lambda: self.fw.fan_speeds["cpu"] == 80)
        self.win.auto_checks["gpu"].click()
        self.wait_for(lambda: self.fw.fan_modes["gpu"] == W.FAN_MODE_AUTO)
        self.wait(2.8)
        self.assertTrue(self.win.fan_buttons["custom"].isChecked())
        self.shot(self.win, "02-custom.png")

    def test_quiet_mode_locks_fans(self):
        W = self.W
        self.win.fan_buttons["max"].click()
        self.wait_for(lambda: self.fw.fan_modes["cpu"] == W.FAN_MODE_MAX)
        self.win.mode_buttons["quiet"].click()
        self.wait_for(lambda: self.fw.fan_modes["cpu"] == W.FAN_MODE_AUTO)
        self.wait(3.0)
        self.assertTrue(self.win.mode_buttons["quiet"].isChecked())
        self.assertTrue(self.win.fan_box.overlay.isVisible())
        self.assertFalse(self.win.fan_buttons["max"].isEnabled())
        self.shot(self.win, "03-quiet.png")

    def test_performance_needs_ac(self):
        (self.world.root / "sys/class/power_supply/ADP1/online").write_text("0\n")
        self.wait(1.5)
        self.assertFalse(self.win.mode_buttons["performance"].isEnabled())
        self.assertIn("secteur", self.win.mode_note.text())

    def test_lighting_dialog(self):
        W = self.W
        dialog = self.win.open_lighting(modal=False)
        self.wait(0.3)
        dialog.dynamic_radio.click()
        dialog.effect_radios["wave"].click()
        dialog.direction_buttons["left"].click()
        dialog.base_swatches[4].click()  # cyan
        self.wait_for(lambda: self.fw.kb_payload[0] == W.KB_EFFECTS["wave"])
        self.wait_for(lambda: self.fw.kb_payload[4] == W.KB_DIRECTIONS["left"])
        self.assertEqual(self.fw.kb_payload[5:8], bytes([0, 255, 255]))
        self.shot(dialog, "04-lighting-dynamic.png")
        dialog.static_radio.click()
        dialog._select_zone(2)
        dialog.base_swatches[3].click()  # green on zone 3
        self.wait_for(lambda: self.fw.zone_colors[2] == (0, 255, 0))
        dialog.zone_checks[0].click()  # zone 1 off
        self.wait_for(lambda: self.fw.zones_enabled == 0b1110)
        self.assertEqual(self.controller.state["keyboard"]["mode"], "static")
        self.assertEqual(self.win.prefs.get("recent_colors")[:2], ["#00FF00", "#00FFFF"])
        self.shot(dialog, "05-lighting-static.png")
        dialog.close()

    def test_dialogs_open_without_service(self):
        from nitrosense.client import DaemonClient
        from nitrosense.gui.app import MainWindow, Prefs
        offline = MainWindow(DaemonClient("/nonexistent/nitrosense.sock"), Prefs(), demo=False)
        offline.show()
        self.wait(1.5)
        self.assertFalse(offline.fan_buttons["max"].isEnabled())
        lighting = offline.open_lighting(modal=False)
        self.assertFalse(lighting.static_radio.isEnabled())
        lighting.close()
        offline.open_settings(modal=False).close()
        offline.close()

    def test_settings_dialog(self):
        dialog = self.win.open_settings(modal=False)
        self.wait(0.3)
        self.assertTrue(dialog.battery.isEnabled())
        dialog.battery.click()
        self.wait_for(lambda: self.fw.battery_health)
        dialog.backlight.click()
        self.wait_for(lambda: self.fw.apge_timeout == 0)
        dialog.fahrenheit.click()
        self.assertEqual(self.win.prefs.get("temp_unit"), "F")
        self.assertIn("°F", self.win.unit_label.text())
        self.assertIn("AN515-58", dialog.diag.toPlainText())
        self.shot(dialog, "06-settings.png")
        dialog.close()


if __name__ == "__main__":
    unittest.main()
