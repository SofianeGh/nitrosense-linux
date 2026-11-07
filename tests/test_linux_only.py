"""Linux-only checks: Unix socket service, peer credentials, real sysfs symlinks."""
import os
import socket
import sys
import tempfile
import threading
import unittest
from pathlib import Path

LINUX = sys.platform.startswith("linux") and hasattr(socket, "SO_PEERCRED")


@unittest.skipUnless(LINUX, "Linux uniquement")
class SocketService(unittest.TestCase):
    def setUp(self):
        from nitrosense.controller import Controller
        from nitrosense.daemon import AccessPolicy, make_server
        from nitrosense.demo import build_fake_tree
        from nitrosense.fake import FakeTransport
        from nitrosense.hardware import Hardware
        from nitrosense.paths import Paths
        from nitrosense.sensors import Sensors

        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        build_fake_tree(root)
        self.fw = FakeTransport()
        paths = Paths(root)
        hw = Hardware(paths, self.fw, {}, sensors=Sensors(paths, nvidia_query=lambda: None))
        self.controller = Controller(hw, root / "state.json", {})
        self.controller.startup()
        self.make_server, self.AccessPolicy = make_server, AccessPolicy
        self.sock_path = str(root / "run" / "nitrosense.sock")
        self.server = None

    def start(self, allowed_users):
        self.server = self.make_server(self.controller, self.sock_path, self.AccessPolicy(allowed_users))
        threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True).start()

    def tearDown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()
        self.tmp.cleanup()

    def test_allowed_user_can_drive_the_fans(self):
        from nitrosense import wmi_codes as W
        from nitrosense.client import DaemonClient
        self.start([str(os.getuid())])
        self.assertEqual(os.stat(self.sock_path).st_mode & 0o777, 0o666)
        client = DaemonClient(self.sock_path)
        self.assertTrue(client.request("ping")["ok"])
        status = client.request("status")
        self.assertTrue(status["ok"], status)
        self.assertEqual(status["caps"]["model"], "Nitro AN515-58")
        response = client.request("set_fan", mode="custom", cpu=55, gpu=65)
        self.assertTrue(response["ok"], response)
        self.assertEqual(self.fw.fan_modes["cpu"], W.FAN_MODE_CUSTOM)
        self.assertEqual(self.fw.fan_speeds, {"cpu": 55, "gpu": 65})

    def test_other_users_are_refused(self):
        from nitrosense.client import DaemonClient
        if os.getuid() == 0:
            self.skipTest("lancé en root")
        self.start(["root"])
        response = DaemonClient(self.sock_path).request("set_fan", mode="max")
        self.assertFalse(response["ok"])
        self.assertIn("non autorisé", response["error"])

    def test_raw_wmi_refused_for_non_root(self):
        from nitrosense.client import DaemonClient
        if os.getuid() == 0:
            self.skipTest("lancé en root")
        self.start([str(os.getuid())])
        response = DaemonClient(self.sock_path).request("raw_wmi", guid="gaming", method=5, input="01020000")
        self.assertFalse(response["ok"])

    def test_missing_service_message(self):
        from nitrosense.client import ClientError, DaemonClient
        with self.assertRaises(ClientError):
            DaemonClient(self.sock_path + ".absent").request("ping")


@unittest.skipUnless(LINUX, "Linux uniquement")
class RealSysfsLayout(unittest.TestCase):
    """Same symlink layout as the kernel: /sys/bus/wmi/devices/<GUID>-N -> platform device."""

    def test_resolve_through_symlinks(self):
        from nitrosense import wmi_codes as W
        from nitrosense.paths import Paths
        from nitrosense.transport import resolve_wmi_method
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            acpi = root / "sys/devices/LNXSYSTM:00/LNXSYBUS:00/PNP0C14:01"
            acpi.mkdir(parents=True)
            (acpi / "path").write_text("\\_SB_.PC00.WMID\n")
            pnp = root / "sys/devices/platform/PNP0C14:01"
            device = pnp / "wmi_bus/wmi_bus-PNP0C14:01" / f"{W.GAMING_GUID}-1"
            device.mkdir(parents=True)
            (device / "object_id").write_text("BH\n")
            os.symlink(os.path.relpath(acpi, pnp), pnp / "firmware_node")
            bus = root / "sys/bus/wmi/devices"
            bus.mkdir(parents=True)
            os.symlink(os.path.relpath(device, bus), bus / f"{W.GAMING_GUID}-1")
            self.assertEqual(resolve_wmi_method(Paths(root), W.GAMING_GUID), "\\_SB_.PC00.WMID.WMBH")


if __name__ == "__main__":
    unittest.main()
