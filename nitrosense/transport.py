"""Raw ACPI-WMI method calls.

The Linux kernel has no generic way to call a WMI method from userspace, so we
use the acpi_call module (/proc/acpi/call) to run the ACPI method behind each
WMI GUID: <PNP0C14 device path>.WM<object_id>(instance, method_id, buffer).
The device path and object id come from /sys/bus/wmi/devices/<GUID>.
"""
from __future__ import annotations

import os
import re
import threading
from pathlib import Path

from . import wmi_codes as W
from .paths import Paths, read_text


class WmiError(RuntimeError):
    pass


class Transport:
    """Base class: run a WMI method and return its raw output bytes."""

    name = "none"

    def available(self) -> bool:
        return False

    def supports(self, guid: str) -> bool:
        """True when this transport can reach the given WMI GUID."""
        return False

    def describe(self) -> dict:
        return {"name": self.name, "available": self.available()}

    def call(self, guid: str, method_id: int, payload: bytes) -> bytes:
        raise WmiError("aucun transport WMI disponible (module acpi_call absent ?)")

    def call_int(self, guid: str, method_id: int, value: int, in_size: int = 8) -> int:
        out = self.call(guid, method_id, int(value).to_bytes(in_size, "little"))
        return int.from_bytes(out[:8].ljust(8, b"\0"), "little")


class NullTransport(Transport):
    name = "none"


_HEX_TOKEN = re.compile(r"0x([0-9a-fA-F]+)")


def parse_acpi_call_result(text: str) -> bytes:
    """Parse what /proc/acpi/call returns after a call.

    Buffers look like "{0x00, 0x8a, 0x09}", integers like "0x1234".
    """
    result = text.replace("\x00", "").strip()
    if not result or result == "not called":
        raise WmiError("acpi_call: aucun résultat")
    if result.startswith("Error"):
        raise WmiError(f"acpi_call: {result}")
    if result.startswith("{"):
        return bytes(int(h, 16) & 0xFF for h in _HEX_TOKEN.findall(result))
    if result.startswith("0x") or result.isdigit():
        return (int(result, 0) & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")
    raise WmiError(f"acpi_call: résultat inattendu {result!r}")


def format_acpi_buffer(payload: bytes) -> str:
    """acpi_call buffer syntax. No spaces allowed inside the braces."""
    return "{" + ",".join(f"0x{b:02x}" for b in payload) + "}"


def _wmi_device_dirs(paths: Paths, guid: str) -> list[Path]:
    guid = guid.upper()
    found: list[Path] = []
    bus = paths.sys("bus", "wmi", "devices")
    if bus.is_dir():
        for entry in sorted(bus.iterdir()):
            name = entry.name.upper()
            if name == guid or name.startswith(guid + "-"):
                found.append(Path(os.path.realpath(entry)))
    if found:
        return found
    # Fallback for trees without the /sys/bus/wmi symlinks (older kernels, tests).
    platform = paths.sys("devices", "platform")
    if platform.is_dir():
        for pnp in sorted(platform.glob("PNP0C14*")):
            for pattern in (guid + "*", "wmi_bus/*/" + guid + "*"):
                found.extend(sorted(pnp.glob(pattern)))
    return found


def guid_present(paths: Paths, guid: str) -> bool:
    return bool(_wmi_device_dirs(paths, guid))


def resolve_wmi_method(paths: Paths, guid: str) -> str:
    """Return the ACPI method path for a WMI method GUID, e.g. \\_SB_.WMID.WMBH."""
    for device in _wmi_device_dirs(paths, guid):
        object_id = read_text(device / "object_id")
        if not object_id or len(object_id) != 2:
            continue
        for parent in (device, *device.parents):
            acpi_path = read_text(parent / "firmware_node" / "path")
            if acpi_path:
                return f"{acpi_path}.WM{object_id}"
    raise WmiError(f"GUID WMI {guid} introuvable dans /sys/bus/wmi/devices")


class AcpiCallTransport(Transport):
    name = "acpi_call"

    def __init__(self, paths: Paths):
        self.paths = paths
        self._lock = threading.Lock()
        self._methods: dict[str, str] = {}

    @property
    def call_file(self) -> Path:
        return self.paths.proc("acpi", "call")

    def available(self) -> bool:
        return self.call_file.exists()

    def method_path(self, guid: str) -> str:
        guid = guid.upper()
        if guid not in self._methods:
            self._methods[guid] = resolve_wmi_method(self.paths, guid)
        return self._methods[guid]

    def supports(self, guid: str) -> bool:
        if not self.available():
            return False
        try:
            self.method_path(guid)
        except WmiError:
            return False
        return True

    def describe(self) -> dict:
        info = super().describe()
        methods = {}
        for alias, guid in W.GUID_ALIASES.items():
            try:
                methods[alias] = self.method_path(guid)
            except WmiError as exc:
                methods[alias] = f"absent ({exc})"
        info["methods"] = methods
        return info

    def build_command(self, guid: str, method_id: int, payload: bytes) -> str:
        # WMxx(Arg0 = instance 0, Arg1 = method id, Arg2 = input buffer)
        return f"{self.method_path(guid)} 0x0 0x{method_id:x} {format_acpi_buffer(payload)}"

    def _exchange(self, command: str) -> str:
        with open(self.call_file, "w", encoding="ascii") as fh:
            fh.write(command)
        with open(self.call_file, "r", encoding="ascii", errors="replace") as fh:
            return fh.read()

    def call(self, guid: str, method_id: int, payload: bytes) -> bytes:
        if not self.available():
            raise WmiError("module acpi_call non chargé (/proc/acpi/call absent)")
        command = self.build_command(guid, method_id, payload)
        with self._lock:
            result = self._exchange(command)
        return parse_acpi_call_result(result)
