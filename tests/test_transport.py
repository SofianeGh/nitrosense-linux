import tempfile
import unittest
from pathlib import Path

from nitrosense import wmi_codes as W
from nitrosense.demo import build_fake_tree
from nitrosense.paths import Paths
from nitrosense.transport import (AcpiCallTransport, WmiError, format_acpi_buffer,
                                  guid_present, parse_acpi_call_result, resolve_wmi_method)


class ParseResult(unittest.TestCase):
    def test_buffer(self):
        self.assertEqual(parse_acpi_call_result("{0x00, 0x8a, 0x09, 0x00}"), b"\x00\x8a\x09\x00")
        self.assertEqual(parse_acpi_call_result("{0x00,0x8a}\x00"), b"\x00\x8a")

    def test_integer(self):
        self.assertEqual(parse_acpi_call_result("0x1234\n"), (0x1234).to_bytes(8, "little"))
        self.assertEqual(parse_acpi_call_result("0x0"), bytes(8))

    def test_errors(self):
        for text in ("Error: AE_NOT_FOUND", "not called", "", '"hello"'):
            with self.assertRaises(WmiError):
                parse_acpi_call_result(text)

    def test_format_buffer_has_no_spaces(self):
        self.assertEqual(format_acpi_buffer(b"\x09\x00\x41\x00"), "{0x09,0x00,0x41,0x00}")


class Resolve(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        build_fake_tree(self.root)
        self.paths = Paths(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_method_path(self):
        self.assertEqual(resolve_wmi_method(self.paths, W.GAMING_GUID), "\\_SB_.WMID.WMBH")
        self.assertEqual(resolve_wmi_method(self.paths, W.APGE_GUID.lower()), "\\_SB_.WMID.WMAA")
        self.assertTrue(guid_present(self.paths, W.BATTERY_GUID))
        with self.assertRaises(WmiError):
            resolve_wmi_method(self.paths, "00000000-0000-0000-0000-000000000000")

    def test_command_and_call(self):
        class Recorder(AcpiCallTransport):
            sent = []

            def _exchange(self, command):
                self.sent.append(command)
                return "{0x00, 0x00, 0x00, 0x00}"

        (self.root / "proc" / "acpi").mkdir(parents=True)
        (self.root / "proc" / "acpi" / "call").write_text("")
        transport = Recorder(self.paths)
        self.assertTrue(transport.supports(W.GAMING_GUID))
        raw = transport.call_int(W.GAMING_GUID, W.SET_GAMING_FAN_BEHAVIOR, W.fan_behavior_all(W.FAN_MODE_MAX))
        self.assertEqual(raw, 0)
        self.assertEqual(transport.sent[-1],
                         "\\_SB_.WMID.WMBH 0x0 0xe {0x09,0x00,0x82,0x00,0x00,0x00,0x00,0x00}")

    def test_unavailable_without_acpi_call(self):
        transport = AcpiCallTransport(self.paths)
        self.assertFalse(transport.available())
        self.assertFalse(transport.supports(W.GAMING_GUID))
        with self.assertRaises(WmiError):
            transport.call(W.GAMING_GUID, 5, b"\x01\x02\x00\x00")


if __name__ == "__main__":
    unittest.main()
