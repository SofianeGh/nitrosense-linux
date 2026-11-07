"""Simulated AN515-58 firmware, used by the tests and by the demo mode.

It answers the same WMI calls as the real BIOS, with the encodings decoded
from NitroSense, so the whole stack can run without the laptop."""
from __future__ import annotations

from . import wmi_codes as W
from .transport import Transport


def _u32(value: int) -> bytes:
    return (value & 0xFFFFFFFF).to_bytes(4, "little")


def _u64(value: int) -> bytes:
    return (value & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")


class FakeTransport(Transport):
    name = "fake"
    # Bitmap 0x33 read on the real BIOS V2.21: Quiet, Default, Performance, Turbo.
    # The real BIOS also accepts Eco (0x06) although it does not list it.
    SUPPORTED_OPMODES = (0x00, 0x01, 0x04, 0x05)
    ACCEPTED_OPMODES = SUPPORTED_OPMODES + (0x06,)

    def __init__(self):
        self.calls: list[tuple[str, int, bytes]] = []
        self.fail_methods: set[int] = set()
        self.profile = 0x01
        self.fan_modes = {"cpu": W.FAN_MODE_AUTO, "gpu": W.FAN_MODE_AUTO}
        self.fan_speeds = {"cpu": 0, "gpu": 0}
        self.kb_payload = bytes(16)
        self.zone_colors = [(255, 0, 0)] * W.KB_ZONES
        self.zones_enabled = 0x0F
        self.apge_brightness = 100
        self.apge_timeout = W.KB_TIMEOUT_SECONDS
        self.battery_health = False
        self.gpu_mux = 1
        self.temps = {"cpu": 52.0, "gpu": 47.0, "system": 40.0}

    def available(self) -> bool:
        return True

    def supports(self, guid: str) -> bool:
        return guid.upper() in (W.GAMING_GUID, W.APGE_GUID, W.BATTERY_GUID)

    def describe(self) -> dict:
        return {"name": self.name, "available": True,
                "methods": {alias: f"\\_SB_.FAKE.WM{alias[:2].upper()}" for alias in W.GUID_ALIASES}}

    # Fan speed model used by the demo: RPM follows the mode / profile.
    def rpm(self, fan: str) -> int:
        mode = self.fan_modes[fan]
        offset = 0 if fan == "cpu" else 180
        if mode == W.FAN_MODE_MAX:
            return 5880 + offset
        if mode == W.FAN_MODE_CUSTOM:
            return int(self.fan_speeds[fan] * 62) + offset // 2
        base = {0x00: 2450, 0x01: 3050, 0x04: 3900, 0x05: 4300}.get(self.profile, 3050)
        heat = max(0.0, self.temps["cpu" if fan == "cpu" else "gpu"] - 50.0) * 45
        return int(base + heat) + offset

    def calls_for(self, method: int) -> list[int]:
        """Integer inputs sent to a gaming method (for the tests)."""
        return [int.from_bytes(p[:8].ljust(8, b"\0"), "little")
                for g, m, p in self.calls if g == W.GAMING_GUID and m == method]

    def call(self, guid: str, method_id: int, payload: bytes) -> bytes:
        guid = guid.upper()
        payload = bytes(payload)
        self.calls.append((guid, method_id, payload))
        value = int.from_bytes(payload[:8].ljust(8, b"\0"), "little")
        if method_id in self.fail_methods:
            return _u32(0x01)
        if guid == W.GAMING_GUID:
            return self._gaming(method_id, value, payload)
        if guid == W.APGE_GUID:
            return self._apge(method_id, value)
        if guid == W.BATTERY_GUID:
            return self._battery(method_id, payload)
        return _u32(0x01)

    def _gaming(self, method: int, value: int, payload: bytes) -> bytes:
        if method == W.SET_GAMING_FAN_BEHAVIOR:
            groups = value & 0xFFFF
            if groups & W.FAN_GROUP_BIT["cpu"]:
                self.fan_modes["cpu"] = (value >> 16) & 0x3
            if groups & W.FAN_GROUP_BIT["gpu"]:
                self.fan_modes["gpu"] = (value >> 22) & 0x3
            return _u32(0)
        if method == W.SET_GAMING_FAN_SPEED:
            fan = {0x01: "cpu", 0x04: "gpu"}.get(value & 0xFF)
            if fan is None:
                return _u32(0x01)
            self.fan_speeds[fan] = (value >> 8) & 0xFF
            return _u32(0)
        if method == W.GET_GAMING_FAN_BEHAVIOR:
            return _u64((self.fan_modes["cpu"] << 8) | (self.fan_modes["gpu"] << 14))
        if method == W.GET_GAMING_FAN_SPEED:
            fan = {0x01: "cpu", 0x04: "gpu"}.get(value & 0xFF)
            return _u64(self.fan_speeds[fan] << 8) if fan else _u64(0x01)
        if method == W.GET_GAMING_SYS_INFO:
            if value & 0xFF == 0x01:
                sensor = (value >> 8) & 0xFF
                readings = {
                    W.SENSOR_CPU_TEMP: int(self.temps["cpu"]),
                    W.SENSOR_CPU_FAN: self.rpm("cpu"),
                    W.SENSOR_SYSTEM_TEMP: int(self.temps["system"]),
                    W.SENSOR_GPU_FAN: self.rpm("gpu"),
                    W.SENSOR_GPU_TEMP: int(self.temps["gpu"]),
                }
                if sensor in readings:
                    return _u64(readings[sensor] << 8)
                return _u64(0x01)
            return _u64(0)
        if method == W.SET_GAMING_MISC_SETTING:
            index, setting = value & 0xFF, (value >> 8) & 0xFF
            if index == W.MISC_PLATFORM_PROFILE and setting in self.ACCEPTED_OPMODES:
                self.profile = setting
                return _u32(0)
            if index == W.MISC_GPU_MODE and setting in (1, 2):
                self.gpu_mux = setting
                return _u32(0)
            return _u32(0x01)
        if method == W.GET_GAMING_MISC_SETTING:
            index = value & 0xFF
            if index == W.MISC_PLATFORM_PROFILE:
                return _u64(self.profile << 8)
            if index == W.MISC_SUPPORTED_PROFILES:
                bitmap = sum(1 << v for v in self.SUPPORTED_OPMODES)
                return _u64(bitmap << 8)
            if index == W.MISC_GPU_MODE:
                return _u64(self.gpu_mux << 8)
            if index == W.MISC_GPU_MODE_SUPPORT:
                return _u64(3 << 8)
            return _u64(0x01)
        if method == W.SET_GAMING_KB_BACKLIGHT:
            if len(payload) != 16:
                return _u32(0x01)
            self.kb_payload = payload
            return _u32(0)
        if method == W.SET_GAMING_RGB_KB:
            color = ((value >> 8) & 0xFF, (value >> 16) & 0xFF, (value >> 24) & 0xFF)
            for index in range(W.KB_ZONES):
                if value & (1 << index):
                    self.zone_colors[index] = color
            return _u32(0)
        if method == W.SET_GAMING_LED_BEHAVIOR:
            self.zones_enabled = (value >> 40) & 0x0F
            return _u32(0)
        return _u32(0x01)

    def _apge(self, method: int, value: int) -> bytes:
        function = value & 0xFF
        if method == W.APGE_GET_FUNCTION and function == 0x01:
            return _u64((self.apge_brightness << 32) | (self.apge_timeout << 40))
        if method == W.APGE_SET_FUNCTION and function == 0x02:
            self.apge_brightness = (value >> 32) & 0xFF
            self.apge_timeout = (value >> 40) & 0xFF
            return _u32(0)
        return _u32(0x01)

    def _battery(self, method: int, payload: bytes) -> bytes:
        if method == W.BATTERY_GET_HEALTH_STATUS:
            return bytes([0x03, 0, 0, 1 if self.battery_health else 0, 0, 0, 0, 0])
        if method == W.BATTERY_SET_HEALTH_CONTROL and len(payload) >= 3:
            if payload[1] & W.BATTERY_HEALTH_MODE:
                self.battery_health = bool(payload[2])
            return bytes(4)
        return bytes([0xFF, 0xFF, 0, 0])
