"""Demo mode: a simulated Nitro AN515-58 (fake /sys tree + fake firmware).

Lets the GUI and the CLI run anywhere, without root and without the laptop:
    python3 -m nitrosense.gui --demo
"""
from __future__ import annotations

import atexit
import math
import os
import random
import shutil
import tempfile
import time
from pathlib import Path

from . import wmi_codes as W
from .controller import Controller
from .fake import FakeTransport
from .hardware import Hardware
from .paths import Paths, read_text
from .sensors import Sensors

PROFILE_CHOICES = "low-power quiet balanced balanced-performance performance"
# sysfs names contain ":" (0000:01:00.0, PNP0C14:00); Windows forbids it in file names.
COLON = "_" if os.name == "nt" else ":"
SYSFS_TO_OPMODE = {"low-power": 0x06, "quiet": 0x00, "balanced": 0x01,
                   "balanced-performance": 0x04, "performance": 0x05}


def _put(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{value}\n", encoding="utf-8")


def build_fake_tree(root: Path, hwmon_fans: bool = True, platform_profile: bool = True) -> None:
    sys_dir = root / "sys"
    dmi = sys_dir / "class" / "dmi" / "id"
    for key, value in (("sys_vendor", "Acer"), ("product_name", "Nitro AN515-58"),
                       ("product_family", "Nitro 5"), ("bios_version", "V2.21"), ("board_name", "Jimny_ADH")):
        _put(dmi / key, value)
    if platform_profile:
        _put(sys_dir / "firmware" / "acpi" / "platform_profile", "balanced")
        _put(sys_dir / "firmware" / "acpi" / "platform_profile_choices", PROFILE_CHOICES)
    core = sys_dir / "class" / "hwmon" / "hwmon3"
    _put(core / "name", "coretemp")
    _put(core / "temp1_label", "Package id 0")
    _put(core / "temp1_input", 52000)
    if hwmon_fans:
        acer = sys_dir / "class" / "hwmon" / "hwmon5"
        _put(acer / "name", "acer")
        _put(acer / "fan1_input", 2600)
        _put(acer / "fan2_input", 2800)
    supplies = sys_dir / "class" / "power_supply"
    _put(supplies / "ADP1" / "type", "Mains")
    _put(supplies / "ADP1" / "online", 1)
    _put(supplies / "BAT1" / "type", "Battery")
    _put(supplies / "BAT1" / "capacity", 96)
    for cpu in range(12):
        _put(sys_dir / "devices" / "system" / "cpu" / f"cpu{cpu}" / "cpufreq" / "scaling_cur_freq", 2400000)
    gpu = sys_dir / "bus" / "pci" / "devices" / f"0000{COLON}01{COLON}00.0"
    _put(gpu / "vendor", "0x10de")
    _put(gpu / "class", "0x030000")
    _put(gpu / "power" / "runtime_status", "active")
    pnp = sys_dir / "devices" / "platform" / f"PNP0C14{COLON}00"
    _put(pnp / "firmware_node" / "path", "\\_SB_.WMID")
    for guid, object_id in ((W.GAMING_GUID, "BH"), (W.APGE_GUID, "AA"), (W.BATTERY_GUID, "BC")):
        _put(pnp / "wmi_bus" / f"wmi_bus-PNP0C14{COLON}00" / f"{guid}-0" / "object_id", object_id)
    _put(root / "proc" / "stat", "cpu  1000 0 500 10000 100 0 0 0 0 0")


class DemoWorld:
    """Moves the fake sensors a bit on every status request."""

    def __init__(self, root: Path, transport: FakeTransport):
        self.root = root
        self.fw = transport
        self.start = time.monotonic()
        self.busy = 1500
        self.idle = 10100
        self.gpu_load = 0.0

    def nvidia(self) -> dict:
        return {"temp": round(self.fw.temps["gpu"]), "load": round(self.gpu_load),
                "mhz": 2250 if self.gpu_load > 5 else 210}

    def tick(self) -> None:
        try:
            self._tick()
        except OSError:  # demo tree already removed (process exiting)
            pass

    def _tick(self) -> None:
        elapsed = time.monotonic() - self.start
        sys_dir = self.root / "sys"
        raw_profile = read_text(sys_dir / "firmware" / "acpi" / "platform_profile", "balanced")
        self.fw.profile = SYSFS_TO_OPMODE.get(raw_profile, 0x01)
        boost = {0x00: -6, 0x01: 0, 0x04: 5, 0x05: 8, 0x06: -8}.get(self.fw.profile, 0)

        load = max(2.0, min(100.0, 20 + 35 * max(0.0, math.sin(elapsed / 8.0)) + random.uniform(-5, 5)))
        self.gpu_load = max(0.0, 60 * math.sin(elapsed / 13.0) + random.uniform(-3, 3))
        cooling = (self.fw.rpm("cpu") - 3000) / 400.0
        cpu_target = 40 + load * 0.45 + boost - cooling
        gpu_target = 38 + self.gpu_load * 0.4 + boost / 2 - cooling / 2
        self.fw.temps["cpu"] += (cpu_target - self.fw.temps["cpu"]) * 0.35
        self.fw.temps["gpu"] += (gpu_target - self.fw.temps["gpu"]) * 0.25

        self.busy += int(load * 12)
        self.idle += int((100 - load) * 12)
        (self.root / "proc" / "stat").write_text(f"cpu  {self.busy} 0 0 {self.idle} 0 0 0 0 0 0\n")
        (sys_dir / "class" / "hwmon" / "hwmon3" / "temp1_input").write_text(f"{int(self.fw.temps['cpu'] * 1000)}\n")
        acer = sys_dir / "class" / "hwmon" / "hwmon5"
        if acer.exists():
            (acer / "fan1_input").write_text(f"{self.fw.rpm('cpu')}\n")
            (acer / "fan2_input").write_text(f"{self.fw.rpm('gpu')}\n")
        mhz = int(1200 + load * 30 + boost * 60 + random.uniform(-80, 80))
        for cpu in range(12):
            path = sys_dir / "devices" / "system" / "cpu" / f"cpu{cpu}" / "cpufreq" / "scaling_cur_freq"
            path.write_text(f"{max(400, mhz - cpu * 40) * 1000}\n")


def make_demo_controller(state_dir: Path | None = None) -> tuple[Controller, DemoWorld]:
    root = Path(tempfile.mkdtemp(prefix="nitrosense-demo-"))
    atexit.register(shutil.rmtree, root, True)
    build_fake_tree(root)
    transport = FakeTransport()
    world = DemoWorld(root, transport)
    paths = Paths(root)
    hardware = Hardware(paths, transport, {}, sensors=Sensors(paths, nvidia_query=world.nvidia))
    state_path = (state_dir or root) / "state.json"
    controller = Controller(hardware, state_path, {}, on_status=world.tick)
    controller.startup()
    return controller, world
