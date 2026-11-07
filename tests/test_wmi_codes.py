"""The encodings must match what NitroSense 3.1 sends on the AN515-58."""
import unittest

from nitrosense import wmi_codes as W


class FanCodes(unittest.TestCase):
    def test_all_fan_modes_match_nitrosense(self):
        # CommonFunction.set_all_fan_mode: 0x9 | 0x410000 / 0x820000 / 0xC30000
        self.assertEqual(W.fan_behavior_all(W.FAN_MODE_AUTO), 0x00410009)
        self.assertEqual(W.fan_behavior_all(W.FAN_MODE_MAX), 0x00820009)
        self.assertEqual(W.fan_behavior_all(W.FAN_MODE_CUSTOM), 0x00C30009)

    def test_single_fan_behavior(self):
        # set_single_custom_fan_state: CPU 0x1 | mode << 16, GPU 0x8 | mode << 22
        self.assertEqual(W.fan_behavior(W.FAN_MODE_CUSTOM, None), 0x00030001)
        self.assertEqual(W.fan_behavior(None, W.FAN_MODE_CUSTOM), 0x00C00008)
        self.assertEqual(W.fan_behavior(W.FAN_MODE_AUTO, W.FAN_MODE_CUSTOM), 0x00C10009)
        with self.assertRaises(ValueError):
            W.fan_behavior(7, None)

    def test_fan_speed(self):
        # set_single_custom_fan_speed: CPU id 1, GPU id 4, percent << 8
        self.assertEqual(W.fan_speed("cpu", 60), 0x3C01)
        self.assertEqual(W.fan_speed("gpu", 100), 0x6404)
        self.assertEqual(W.fan_speed("gpu", 150), 0x6404)
        self.assertEqual(W.fan_speed("cpu", -5), 0x0001)


class SensorCodes(unittest.TestCase):
    def test_sysinfo(self):
        self.assertEqual(W.sysinfo_query(W.SENSOR_CPU_FAN), 0x0201)
        self.assertEqual(W.sysinfo_query(W.SENSOR_GPU_FAN), 0x0601)
        self.assertEqual(W.sysinfo_query(W.SENSOR_GPU_TEMP), 0x0A01)
        self.assertEqual(W.parse_sysinfo(0x0F8A00), 0x0F8A)
        self.assertIsNone(W.parse_sysinfo(0x0F8A01))

    def test_misc(self):
        self.assertEqual(W.misc_set(W.MISC_PLATFORM_PROFILE, W.OPMODE["performance"]), 0x040B)
        self.assertEqual(W.misc_set(W.MISC_GPU_MODE, 2), 0x0202)
        self.assertEqual(W.parse_misc(0x0400), 4)
        self.assertIsNone(W.parse_misc(0x0401))
        self.assertEqual(W.parse_supported_profiles(0x1300), ["quiet", "default", "performance"])


class KeyboardCodes(unittest.TestCase):
    @staticmethod
    def nitrosense_dynamic(pattern, speed, level, direction, rgb):
        """LightingDynamicUI.Set_Dynamic_Keyboard(), transcribed from the IL."""
        r, g, b = rgb
        bright = (level - 1) * 25
        if pattern == 0:   # breathing
            value = 1 | speed << 8 | bright << 16 | r << 40 | g << 48 | b << 56
        elif pattern == 4:  # neon
            value = 2 | speed << 8 | bright << 16
        elif pattern == 1:  # wave
            value = 3 | speed << 8 | bright << 16 | 0x8000000 | direction << 32 | r << 40 | g << 48 | b << 56
        elif pattern == 3:  # shifting
            value = 4 | speed << 8 | bright << 16 | direction << 32 | r << 40 | g << 48 | b << 56
        else:               # zoom
            value = 5 | speed << 8 | bright << 16 | r << 40 | g << 48 | b << 56
        return value.to_bytes(8, "little") + bytes(8)

    def test_effects_match_nitrosense(self):
        rgb = (0x12, 0x34, 0x56)
        cases = [("breathing", 0), ("neon", 4), ("wave", 1), ("shifting", 3), ("zoom", 2)]
        for effect, pattern in cases:
            for direction, code in W.KB_DIRECTIONS.items():
                got = W.kb_backlight_payload(effect, brightness=75, speed=5, direction=direction, color=rgb)
                self.assertEqual(got, self.nitrosense_dynamic(pattern, 5, 4, code, rgb), (effect, direction))

    def test_static_brightness(self):
        # Popup_Lighting.Change_Keyboard_Brightness: (level - 1) * 25 << 16
        self.assertEqual(W.kb_backlight_payload("static", brightness=100), bytes([0, 0, 100]) + bytes(13))
        self.assertEqual(len(W.kb_backlight_payload("static", brightness=0)), 16)

    def test_zone_color_and_enable(self):
        # Popup_Lighting.Change_Zone_Color: zone bit | R << 8 | G << 16 | B << 24
        self.assertEqual(W.rgb_zone(W.zone_bit(0), "#FF8000"), 0x0080FF01)
        self.assertEqual(W.rgb_zone(W.zone_bit(3), (1, 2, 3)), 0x03020108)
        # Change_Zone_Status: 0x8 | zone n -> bit 40 + n
        self.assertEqual(W.zones_enable([True, False, True, False]), 0x08 | 1 << 40 | 1 << 42)
        self.assertEqual(W.zones_enable([True] * 4), 0x0F0000000008)
        with self.assertRaises(ValueError):
            W.zone_bit(4)

    def test_bad_inputs(self):
        with self.assertRaises(ValueError):
            W.kb_backlight_payload("disco")
        with self.assertRaises(ValueError):
            W.parse_color("#12345")


class OtherCodes(unittest.TestCase):
    def test_backlight_timeout(self):
        # get_backlight_off / Backlightoff_Checkbox_Checked, BK_Hotkey_Number = 0x84
        self.assertEqual(W.apge_kb_backlight_query(), 0x88401)
        self.assertEqual(W.apge_kb_backlight_set(100, True), 0x1E6400088402)
        self.assertEqual(W.apge_kb_backlight_set(100, False), 0x006400088402)
        info = W.parse_apge_kb_backlight(0x1E6400000000)
        self.assertEqual(info, {"brightness": 100, "timeout": 30, "timeout_enabled": True})

    def test_battery(self):
        self.assertEqual(W.battery_health_query(), bytes([1, 1, 0, 0]))
        self.assertEqual(W.battery_health_set(True), bytes([1, 1, 1, 0, 0, 0, 0, 0]))
        self.assertEqual(W.battery_health_set(False), bytes([1, 1, 0, 0, 0, 0, 0, 0]))
        parsed = W.parse_battery_health(bytes([3, 0, 0, 1, 0, 0, 0, 0]))
        self.assertTrue(parsed["health_mode"])
        self.assertIsNone(W.parse_battery_health(b"\x00"))


if __name__ == "__main__":
    unittest.main()
