"""Sensors readable from sysfs/procfs: temperatures, load, clocks, fans, power."""
from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

from .paths import Paths, read_int, read_text

NVIDIA_VENDOR = "0x10de"


def find_hwmon(paths: Paths, name: str) -> list[Path]:
    base = paths.sys("class", "hwmon")
    if not base.is_dir():
        return []
    return [d for d in sorted(base.glob("hwmon*")) if read_text(d / "name") == name]


def _index(path: Path, prefix: str) -> int | None:
    match = re.match(rf"{prefix}(\d+)_", path.name)
    return int(match.group(1)) if match else None


class CpuLoadMeter:
    """CPU usage in percent, from two consecutive reads of /proc/stat."""

    def __init__(self, paths: Paths):
        self.paths = paths
        self._previous: tuple[int, int] | None = None

    def sample(self) -> float | None:
        line = (read_text(self.paths.proc("stat"), "") or "").splitlines()
        if not line or not line[0].startswith("cpu "):
            return None
        fields = [int(x) for x in line[0].split()[1:9]]
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        total = sum(fields)
        previous, self._previous = self._previous, (total, idle)
        if previous is None:
            return None
        d_total, d_idle = total - previous[0], idle - previous[1]
        if d_total <= 0:
            return None
        return max(0.0, min(100.0, 100.0 * (d_total - d_idle) / d_total))


class Sensors:
    def __init__(self, paths: Paths, nvidia_query=None):
        self.paths = paths
        self.load = CpuLoadMeter(paths)
        self.load.sample()  # prime it so the first status already has a value
        self._nvidia_query = nvidia_query or self._nvidia_smi

    # ---- CPU ----
    def cpu_temp(self) -> float | None:
        for hw in find_hwmon(self.paths, "coretemp"):
            for label in sorted(hw.glob("temp*_label")):
                if (read_text(label, "") or "").startswith("Package"):
                    value = read_int(label.with_name(label.name.replace("_label", "_input")))
                    if value is not None:
                        return value / 1000.0
            value = read_int(hw / "temp1_input")
            if value is not None:
                return value / 1000.0
        for hw in find_hwmon(self.paths, "k10temp"):
            value = read_int(hw / "temp1_input")
            if value is not None:
                return value / 1000.0
        temps = self.acer_temps()
        return temps.get("cpu")

    def cpu_mhz(self) -> int | None:
        """Highest current core clock (closest to what NitroSense displays)."""
        cpus = self.paths.sys("devices", "system", "cpu")
        values = [read_int(p) for p in cpus.glob("cpu[0-9]*/cpufreq/scaling_cur_freq")]
        values = [v for v in values if v]
        return int(max(values) / 1000) if values else None

    def cpu_load(self) -> float | None:
        return self.load.sample()

    # ---- Acer hwmon (acer-wmi, predator_v4 mode) ----
    def acer_hwmon(self) -> Path | None:
        found = find_hwmon(self.paths, "acer")
        return found[0] if found else None

    def _channel_key(self, hw: Path, kind: str, index: int) -> str | None:
        label = (read_text(hw / f"{kind}{index}_label", "") or "").lower()
        if "gpu" in label:
            return "gpu"
        if "cpu" in label:
            return "cpu"
        if label:
            return None
        if kind == "fan":
            return {1: "cpu", 2: "gpu"}.get(index)
        return "cpu" if index == 1 else None

    def acer_fan_rpm(self) -> dict:
        hw = self.acer_hwmon()
        result: dict = {}
        if hw is None:
            return result
        for path in sorted(hw.glob("fan*_input")):
            index = _index(path, "fan")
            key = self._channel_key(hw, "fan", index) if index else None
            value = read_int(path)
            if key and value is not None and key not in result:
                result[key] = value
        return result

    def acer_temps(self) -> dict:
        hw = self.acer_hwmon()
        result: dict = {}
        if hw is None:
            return result
        for path in sorted(hw.glob("temp*_input")):
            index = _index(path, "temp")
            key = self._channel_key(hw, "temp", index) if index else None
            value = read_int(path)
            if key and value is not None and key not in result:
                result[key] = value / 1000.0
        return result

    def acer_pwm(self) -> dict:
        """{'cpu': (pwm_file, enable_file), 'gpu': (...)} when the kernel exposes fan control."""
        hw = self.acer_hwmon()
        result: dict = {}
        if hw is None:
            return result
        for enable in sorted(hw.glob("pwm*_enable")):
            match = re.match(r"pwm(\d+)_enable", enable.name)
            if not match:
                continue
            index = int(match.group(1))
            pwm = hw / f"pwm{index}"
            key = self._channel_key(hw, "fan", index)
            if key and pwm.exists():
                result[key] = (pwm, enable)
        return result

    # ---- NVIDIA dGPU ----
    def nvidia_device(self) -> Path | None:
        base = self.paths.sys("bus", "pci", "devices")
        if not base.is_dir():
            return None
        for dev in sorted(base.iterdir()):
            if read_text(dev / "vendor") == NVIDIA_VENDOR and (read_text(dev / "class", "") or "").startswith("0x03"):
                return dev
        return None

    def dgpu_state(self) -> str:
        """'active', 'suspended' (do not wake it up) or 'absent'."""
        dev = self.nvidia_device()
        if dev is None:
            return "absent"
        status = read_text(dev / "power" / "runtime_status")
        return "suspended" if status == "suspended" else "active"

    @staticmethod
    def _nvidia_smi() -> dict | None:
        exe = shutil.which("nvidia-smi")
        if not exe:
            return None
        try:
            proc = subprocess.run(
                [exe, "--query-gpu=temperature.gpu,utilization.gpu,clocks.gr",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3, check=False)
        except (OSError, subprocess.SubprocessError):
            return None
        first = proc.stdout.strip().splitlines()[:1]
        if proc.returncode != 0 or not first:
            return None
        parts = [p.strip() for p in first[0].split(",")]

        def num(text):
            try:
                return float(text)
            except ValueError:
                return None
        if len(parts) < 3:
            return None
        return {"temp": num(parts[0]), "load": num(parts[1]), "mhz": num(parts[2])}

    def gpu(self) -> dict:
        state = self.dgpu_state()
        info = {"state": state, "temp": None, "load": None, "mhz": None}
        if state == "active":
            data = self._nvidia_query()
            if data:
                info.update(data)
        if info["temp"] is None:
            info["temp"] = self.acer_temps().get("gpu")
        return info

    # ---- Power ----
    def power(self) -> dict:
        ac = None
        battery = None
        base = self.paths.sys("class", "power_supply")
        if base.is_dir():
            for dev in sorted(base.iterdir()):
                kind = read_text(dev / "type")
                if kind == "Mains":
                    online = read_int(dev / "online")
                    if online is not None:
                        ac = bool(online) or bool(ac)
                elif kind == "Battery" and battery is None:
                    battery = read_int(dev / "capacity")
        return {"ac": ac, "battery": battery}

    def battery_threshold_file(self) -> Path | None:
        base = self.paths.sys("class", "power_supply")
        if not base.is_dir():
            return None
        for dev in sorted(base.iterdir()):
            path = dev / "charge_control_end_threshold"
            if read_text(dev / "type") == "Battery" and path.exists():
                return path
        return None

    def dmi(self) -> dict:
        base = self.paths.sys("class", "dmi", "id")
        return {key: read_text(base / key, "") or "" for key in
                ("sys_vendor", "product_name", "product_family", "bios_version", "board_name")}
