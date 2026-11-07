"""Controller + Hardware against a fake /sys tree and the simulated firmware."""
import copy
import tempfile
import unittest
from pathlib import Path

from nitrosense import wmi_codes as W
from nitrosense.controller import DEFAULT_KEYBOARD, Controller
from nitrosense.demo import COLON, build_fake_tree
from nitrosense.fake import FakeTransport
from nitrosense.hardware import Hardware
from nitrosense.paths import Paths, read_text
from nitrosense.sensors import Sensors


def nvidia_stub():
    return {"temp": 50.0, "load": 10.0, "mhz": 1500.0}


class FakeClock:
    """(monotonic, boottime) pair; boottime alone advances during a suspend."""

    def __init__(self):
        self.mono = 1000.0
        self.boot = 1000.0

    def __call__(self):
        return self.mono, self.boot

    def advance(self, seconds, suspended=0.0):
        self.mono += seconds
        self.boot += seconds + suspended


class Base(unittest.TestCase):
    platform_profile = True
    hwmon_fans = True

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        build_fake_tree(self.root, hwmon_fans=self.hwmon_fans, platform_profile=self.platform_profile)
        self.fw = FakeTransport()
        self.clock = FakeClock()
        self.state_path = self.root / "state.json"
        self.ctl = self.make_controller(self.fw)

    def make_controller(self, fw, config=None):
        paths = Paths(self.root)
        hw = Hardware(paths, fw, config or {}, sensors=Sensors(paths, nvidia_query=nvidia_stub))
        ctl = Controller(hw, self.state_path, config or {}, clock=self.clock)
        ctl.startup()
        return ctl

    def tearDown(self):
        self.tmp.cleanup()

    def ok(self, cmd, uid=1000, **params):
        response = self.ctl.handle({"cmd": cmd, **params}, uid)
        self.assertTrue(response["ok"], response)
        return response

    def fails(self, cmd, uid=1000, **params):
        response = self.ctl.handle({"cmd": cmd, **params}, uid)
        self.assertFalse(response["ok"], response)
        return response["error"]

    def put(self, rel, value):
        (self.root / rel).write_text(f"{value}\n")


class Detection(Base):
    def test_caps(self):
        caps = self.ctl.hw.caps
        self.assertTrue(caps["model_ok"])
        self.assertEqual(caps["profile"], "sysfs")
        self.assertEqual(caps["profiles"], ["quiet", "default", "performance"])
        self.assertEqual(caps["extra_profiles"], ["turbo"])
        self.assertEqual(caps["bios_profiles"], ["quiet", "default", "performance", "turbo"])
        self.assertEqual(caps["fan_rpm"], "hwmon")
        self.assertEqual(caps["fan_control"], "wmi")
        self.assertEqual(caps["keyboard"], "wmi")
        self.assertEqual(caps["backlight_timeout"], "wmi")
        self.assertEqual(caps["battery_limit"], "wmi")
        self.assertEqual(caps["gpu_mux"], {"supported": True, "value": 1, "mode": "hybrid"})

    def test_status(self):
        st = self.ok("status")
        self.assertEqual(st["profile"], "default")
        self.assertEqual(st["profile_raw"], "balanced")
        self.assertEqual(st["rpm"], {"cpu": 2600, "gpu": 2800})
        self.assertAlmostEqual(st["cpu"]["temp"], 52.0)
        self.assertEqual(st["gpu"]["state"], "active")
        self.assertEqual(st["gpu"]["temp"], 50.0)
        self.assertTrue(st["ac"])
        self.assertEqual(st["keyboard"], DEFAULT_KEYBOARD)
        self.assertFalse(st["keyboard_set"])
        self.assertTrue(st["backlight_timeout"])
        self.assertFalse(st["battery_limit"])

    def test_suspended_gpu_is_not_queried(self):
        self.put(f"sys/bus/pci/devices/0000{COLON}01{COLON}00.0/power/runtime_status", "suspended")
        st = self.ok("status")
        self.assertEqual(st["gpu"]["state"], "suspended")
        self.assertIsNone(st["gpu"]["load"])

    def test_unknown_model_blocks_writes(self):
        self.put("sys/class/dmi/id/product_name", "Aspire 5")
        self.ctl = self.make_controller(FakeTransport())
        self.assertIn("non reconnu", self.fails("set_fan", mode="max"))
        self.ok("status")

    def test_unknown_command(self):
        self.assertIn("inconnue", self.fails("rm_rf"))
        self.assertIn("inconnue", self.fails("__class__"))


class Profiles(Base):
    def test_performance_uses_balanced_performance(self):
        self.ok("set_profile", name="performance")
        self.assertEqual(read_text(self.root / "sys/firmware/acpi/platform_profile"), "balanced-performance")
        self.assertEqual(self.ok("status")["profile"], "performance")

    def test_performance_needs_ac(self):
        self.put("sys/class/power_supply/ADP1/online", 0)
        self.assertIn("secteur", self.fails("set_profile", name="performance"))
        self.ok("set_profile", name="quiet")

    def test_performance_needs_40_percent(self):
        self.put("sys/class/power_supply/BAT1/capacity", 30)
        self.assertIn("40", self.fails("set_profile", name="performance"))

    def test_quiet_forces_fans_auto(self):
        self.ok("set_fan", mode="max")
        self.assertEqual(self.fw.fan_modes, {"cpu": W.FAN_MODE_MAX, "gpu": W.FAN_MODE_MAX})
        self.ok("set_profile", name="quiet")
        self.assertEqual(self.fw.fan_modes, {"cpu": W.FAN_MODE_AUTO, "gpu": W.FAN_MODE_AUTO})
        self.assertEqual(self.ok("status")["fan"]["mode"], "auto")
        self.assertIn("Silencieux", self.fails("set_fan", mode="custom"))
        self.ok("set_fan", mode="auto")

    def test_bad_profile(self):
        self.fails("set_profile", name="ludicrous")
        self.fails("set_profile", name="eco")

    def test_turbo(self):
        self.ok("set_profile", name="turbo")
        self.assertEqual(read_text(self.root / "sys/firmware/acpi/platform_profile"), "performance")
        self.assertEqual(self.ok("status")["profile"], "turbo")

    def test_turbo_needs_ac(self):
        self.put("sys/class/power_supply/ADP1/online", 0)
        self.assertIn("secteur", self.fails("set_profile", name="turbo"))

    def test_turbo_refused_when_bios_lacks_it(self):
        fw = FakeTransport()
        fw.SUPPORTED_OPMODES = (0x00, 0x01, 0x04)
        self.ctl = self.make_controller(fw)
        self.assertEqual(self.ctl.hw.caps["extra_profiles"], [])
        self.assertIn("non pris en charge", self.fails("set_profile", name="turbo"))


class ProfileSetElsewhere(Base):
    """KDE / power-profiles-daemon or the mode key change platform_profile."""

    def profile(self):
        return read_text(self.root / "sys/firmware/acpi/platform_profile")

    def test_eco_is_replaced_by_quiet(self):
        self.ok("set_fan", mode="custom", cpu=40, gpu=40)
        self.put("sys/firmware/acpi/platform_profile", "low-power")
        self.ctl.tick()
        self.assertEqual(self.profile(), "quiet")
        self.assertEqual(self.fw.fan_modes, {"cpu": W.FAN_MODE_AUTO, "gpu": W.FAN_MODE_AUTO})

    def test_eco_kept_when_disabled_in_config(self):
        self.ctl = self.make_controller(FakeTransport(), {"eco_to_quiet": False})
        self.put("sys/firmware/acpi/platform_profile", "low-power")
        self.ctl.tick()
        self.assertEqual(self.profile(), "low-power")

    def test_untouched_profile_is_not_followed(self):
        self.clock.advance(60)
        self.put("sys/firmware/acpi/platform_profile", "quiet")
        self.ctl.tick()
        self.assertIsNone(self.ctl.state["profile"])
        self.assertEqual(self.profile(), "quiet")

    def test_change_after_boot_is_followed(self):
        self.ok("set_profile", name="performance")
        self.clock.advance(60)
        self.put("sys/firmware/acpi/platform_profile", "performance")  # KDE "Performance"
        self.ctl.tick()
        self.assertEqual(self.ctl.state["profile"], "turbo")
        self.assertEqual(self.profile(), "performance")
        # Saved: survives a restart of the service.
        self.put("sys/firmware/acpi/platform_profile", "balanced")
        self.ctl = self.make_controller(FakeTransport())
        self.assertEqual(self.profile(), "performance")

    def test_change_right_after_boot_is_reverted(self):
        self.ok("set_profile", name="performance")
        self.ctl = self.make_controller(FakeTransport())  # service (re)start
        self.clock.advance(5)
        self.put("sys/firmware/acpi/platform_profile", "balanced")  # power-profiles-daemon
        self.ctl.tick()
        self.assertEqual(self.profile(), "balanced-performance")
        self.assertEqual(self.ctl.state["profile"], "performance")

    def test_resume_reapplies_everything(self):
        self.ok("set_profile", name="performance")
        self.ok("set_fan", mode="custom", cpu=65, gpu=75)
        self.clock.advance(60)
        self.ctl.tick()
        # Suspend for 10 min; the firmware forgets the fans and the mode.
        self.fw.fan_modes = {"cpu": W.FAN_MODE_AUTO, "gpu": W.FAN_MODE_AUTO}
        self.put("sys/firmware/acpi/platform_profile", "balanced")
        self.clock.advance(2, suspended=600)
        self.ctl.tick()
        self.assertEqual(self.profile(), "balanced-performance")
        self.assertEqual(self.ctl.state["profile"], "performance")
        self.assertEqual(self.fw.fan_modes, {"cpu": W.FAN_MODE_CUSTOM, "gpu": W.FAN_MODE_CUSTOM})
        self.assertEqual(self.fw.fan_speeds, {"cpu": 65, "gpu": 75})


class WmiOnlyProfiles(Base):
    platform_profile = False
    hwmon_fans = False

    def test_profile_and_rpm_through_wmi(self):
        caps = self.ctl.hw.caps
        self.assertEqual(caps["profile"], "wmi")
        self.assertEqual(caps["fan_rpm"], "wmi")
        self.assertEqual(caps["profiles"], ["quiet", "default", "performance"])
        self.ok("set_profile", name="performance")
        self.assertEqual(self.fw.profile, 0x04)
        self.assertIn(0x040B, self.fw.calls_for(W.SET_GAMING_MISC_SETTING))
        st = self.ok("status")
        self.assertEqual(st["profile"], "performance")
        self.assertGreater(st["rpm"]["cpu"], 1000)


class Fans(Base):
    def test_custom_sequence_matches_nitrosense(self):
        self.ok("set_fan", mode="custom", cpu=60, gpu=70)
        self.assertEqual(self.fw.calls_for(W.SET_GAMING_FAN_BEHAVIOR)[-1], 0x00C30009)
        self.assertEqual(self.fw.calls_for(W.SET_GAMING_FAN_SPEED)[-2:], [0x3C01, 0x4604])
        self.assertEqual(self.fw.fan_speeds, {"cpu": 60, "gpu": 70})

    def test_custom_with_one_fan_auto(self):
        self.ok("set_fan", mode="custom", cpu=40, gpu=80, cpu_auto=True)
        self.assertEqual(self.fw.calls_for(W.SET_GAMING_FAN_BEHAVIOR)[-1], 0x00C10009)
        self.assertEqual(self.fw.fan_modes, {"cpu": W.FAN_MODE_AUTO, "gpu": W.FAN_MODE_CUSTOM})

    def test_slider_update_only_sends_speeds(self):
        self.ok("set_fan", mode="custom", cpu=50, gpu=50)
        before = len(self.fw.calls_for(W.SET_GAMING_FAN_BEHAVIOR))
        self.ok("set_fan_speed", cpu=90, gpu=30)
        self.assertEqual(len(self.fw.calls_for(W.SET_GAMING_FAN_BEHAVIOR)), before)
        self.assertEqual(self.fw.fan_speeds, {"cpu": 90, "gpu": 30})

    def test_slider_needs_custom_mode(self):
        self.assertIn("Perso", self.fails("set_fan_speed", cpu=10))

    def test_validation(self):
        self.fails("set_fan", mode="turbo")
        self.fails("set_fan", mode="custom", cpu=101)
        self.fails("set_fan", mode="custom", cpu="abc")

    def test_bios_refusal_is_reported(self):
        self.fw.fail_methods.add(W.SET_GAMING_FAN_BEHAVIOR)
        self.assertIn("refusé", self.fails("set_fan", mode="max"))
        self.assertEqual(self.ok("status")["fan"]["mode"], "auto")

    def test_shutdown_restores_auto(self):
        self.ok("set_fan", mode="custom", cpu=30, gpu=30)
        self.ctl.shutdown()
        self.assertEqual(self.fw.fan_modes, {"cpu": W.FAN_MODE_AUTO, "gpu": W.FAN_MODE_AUTO})


class ThermalGuard(Base):
    def test_guard_trips_and_recovers(self):
        self.ok("set_fan", mode="custom", cpu=30, gpu=30)
        self.put("sys/class/hwmon/hwmon3/temp1_input", 95000)
        self.ctl.tick()
        self.assertTrue(self.ctl.guard_active)
        self.assertEqual(self.fw.fan_modes["cpu"], W.FAN_MODE_AUTO)
        self.put("sys/class/hwmon/hwmon3/temp1_input", 70000)
        self.fw.temps["gpu"] = 60
        for _ in range(15):
            self.ctl.tick()
        self.assertFalse(self.ctl.guard_active)
        self.assertEqual(self.fw.fan_modes["cpu"], W.FAN_MODE_CUSTOM)

    def test_quiet_set_elsewhere_resets_fans(self):
        self.ok("set_fan", mode="max")
        self.put("sys/firmware/acpi/platform_profile", "quiet")
        self.ctl.tick()
        self.assertEqual(self.fw.fan_modes["cpu"], W.FAN_MODE_AUTO)


class Keyboard(Base):
    def kb(self, **changes):
        kb = copy.deepcopy(DEFAULT_KEYBOARD)
        kb.update(changes)
        return kb

    def test_static_zones(self):
        zones = [{"on": True, "color": "#FF0000"}, {"on": True, "color": "#00ff00"},
                 {"on": False, "color": "#0000FF"}, {"on": True, "color": "#FFFFFF"}]
        self.ok("set_keyboard", keyboard=self.kb(zones=zones, brightness=50))
        self.assertEqual(self.fw.kb_payload, bytes([0, 0, 50]) + bytes(13))
        self.assertEqual(self.fw.zone_colors, [(255, 0, 0), (0, 255, 0), (0, 0, 0), (255, 255, 255)])
        self.assertEqual(self.fw.zones_enabled, 0b1011)
        st = self.ok("status")
        self.assertTrue(st["keyboard_set"])
        self.assertEqual(st["keyboard"]["zones"][1]["color"], "#00FF00")

    def test_effect(self):
        self.ok("set_keyboard", keyboard=self.kb(mode="effect", effect="wave", speed=7,
                                                 direction="left", color="#102030", brightness=100))
        self.assertEqual(self.fw.kb_payload[:8], bytes([3, 7, 100, 8, 2, 0x10, 0x20, 0x30]))

    def test_off(self):
        self.ok("set_keyboard", keyboard=self.kb(mode="off"))
        self.assertEqual(self.fw.kb_payload[2], 0)

    def test_validation(self):
        self.fails("set_keyboard", keyboard=self.kb(effect="disco", mode="effect"))
        self.fails("set_keyboard", keyboard=self.kb(zones=[]))
        self.fails("set_keyboard", keyboard=self.kb(speed=0))
        self.fails("set_keyboard", keyboard="red")


class Extras(Base):
    def test_backlight_timeout(self):
        self.ok("set_backlight_timeout", enabled=False)
        self.assertEqual(self.fw.apge_timeout, 0)
        self.assertEqual(self.fw.apge_brightness, 100)
        self.assertFalse(self.ok("status")["backlight_timeout"])

    def test_battery_limit(self):
        self.ok("set_battery_limit", enabled=True)
        self.assertTrue(self.fw.battery_health)
        self.assertTrue(self.ok("status")["battery_limit"])

    def test_battery_limit_sysfs_preferred(self):
        self.put("sys/class/power_supply/BAT1/charge_control_end_threshold", 100)
        self.ctl = self.make_controller(self.fw)
        self.assertEqual(self.ctl.hw.caps["battery_limit"], "sysfs")
        self.ok("set_battery_limit", enabled=True)
        self.assertEqual(read_text(self.root / "sys/class/power_supply/BAT1/charge_control_end_threshold"), "80")

    def test_raw_wmi_is_root_only(self):
        self.assertIn("root", self.fails("raw_wmi", guid="gaming", method=5, input="01020000"))
        out = self.ok("raw_wmi", uid=0, guid="gaming", method=5, input="01020000")
        self.assertEqual(int(out["output_u64"], 16) & 0xFF, 0)

    def test_selftest(self):
        report = self.ok("selftest")["report"]
        self.assertIn("wmi.sysinfo.cpu_fan", report["checks"])
        self.assertEqual(report["checks"]["sysfs.platform_profile"], "balanced")


class Persistence(Base):
    def test_settings_survive_a_reboot(self):
        self.ok("set_profile", name="performance")
        self.ok("set_fan", mode="custom", cpu=65, gpu=75)
        kb = copy.deepcopy(DEFAULT_KEYBOARD)
        kb.update(mode="effect", effect="breathing", color="#00FFFF")
        self.ok("set_keyboard", keyboard=kb)
        self.ok("set_battery_limit", enabled=True)
        # "Reboot": firmware back to defaults, platform profile back to balanced.
        self.put("sys/firmware/acpi/platform_profile", "balanced")
        fresh = FakeTransport()
        self.ctl = self.make_controller(fresh)
        self.assertEqual(read_text(self.root / "sys/firmware/acpi/platform_profile"), "balanced-performance")
        self.assertEqual(fresh.fan_modes, {"cpu": W.FAN_MODE_CUSTOM, "gpu": W.FAN_MODE_CUSTOM})
        self.assertEqual(fresh.fan_speeds, {"cpu": 65, "gpu": 75})
        self.assertEqual(fresh.kb_payload[0], W.KB_EFFECTS["breathing"])
        self.assertTrue(fresh.battery_health)

    def test_untouched_settings_are_not_reapplied(self):
        fresh = FakeTransport()
        self.ctl = self.make_controller(fresh)
        writes = {W.SET_GAMING_FAN_BEHAVIOR, W.SET_GAMING_FAN_SPEED, W.SET_GAMING_KB_BACKLIGHT,
                  W.SET_GAMING_RGB_KB, W.SET_GAMING_MISC_SETTING}
        sets = [m for g, m, _ in fresh.calls if g == W.GAMING_GUID and m in writes]
        sets += [m for g, m, _ in fresh.calls if g == W.BATTERY_GUID and m == W.BATTERY_SET_HEALTH_CONTROL]
        sets += [m for g, m, p in fresh.calls if g == W.APGE_GUID and p[:1] == b"\x02"]
        self.assertEqual(sets, [])


if __name__ == "__main__":
    unittest.main()
