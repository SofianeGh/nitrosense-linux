"""Hardware layer: picks a backend for each feature and exposes the
NitroSense operations (modes, fans, keyboard, battery limit...).

Backends, in order of preference:
- profile:      kernel platform_profile (acer-wmi)  -> raw WMI (acpi_call)
- fan RPM:      acer hwmon (acer-wmi)               -> raw WMI
- fan control:  raw WMI with NitroSense's exact codes -> acer hwmon pwm
- keyboard, backlight timeout, GPU MUX (read only): raw WMI only
- battery 80% limit: power_supply charge_control_end_threshold -> raw WMI
"""
from __future__ import annotations

import logging
import platform

from . import TARGET_MODEL
from . import wmi_codes as W
from .paths import Paths, read_int, read_text, write_text
from .sensors import Sensors
from .transport import Transport, WmiError

log = logging.getLogger("nitrosense.hw")

PROFILES = ("quiet", "default", "performance")
PROFILE_TO_SYSFS = {
    "quiet": ("quiet", "low-power"),
    "default": ("balanced",),
    # acer-wmi maps balanced-performance to the 0x04 "Performance" mode that
    # NitroSense uses here; plain "performance" is Turbo (0x05) in that driver.
    "performance": ("balanced-performance", "performance"),
}
SYSFS_TO_PROFILE = {
    "low-power": "eco", "quiet": "quiet", "cool": "quiet", "balanced": "default",
    "balanced-performance": "performance", "performance": "turbo",
}


class HardwareError(RuntimeError):
    """Error whose message is shown to the user as is."""


class Hardware:
    def __init__(self, paths: Paths, transport: Transport, config: dict | None = None,
                 sensors: Sensors | None = None):
        self.paths = paths
        self.transport = transport
        self.config = config or {}
        self.sensors = sensors or Sensors(paths)
        self.caps: dict = {}
        self._profile_map: dict[str, str] = {}

    # ------------------------------------------------------------------ utils
    def _hotkey(self) -> int:
        return int(self.config.get("kb_hotkey_number", W.KB_HOTKEY_NUMBER))

    def _check_writes(self) -> None:
        if not self.caps.get("writes_allowed"):
            raise HardwareError(
                f"Modèle non reconnu ({self.caps.get('model')}) : écriture désactivée par sécurité. "
                "Mettre \"allow_unknown_model\": true dans /etc/nitrosense/config.json pour forcer.")

    def _wmi_get(self, method: int, value: int) -> int:
        return self.transport.call_int(W.GAMING_GUID, method, value, in_size=4)

    def _wmi_set(self, method: int, value: int) -> int:
        raw = self.transport.call_int(W.GAMING_GUID, method, value, in_size=8)
        if not W.status_ok(raw):
            raise HardwareError(f"Le BIOS a refusé la commande WMI {method} "
                                f"(entrée 0x{value:X}, statut 0x{raw & 0xFF:02X})")
        return raw

    def _try(self, fn, default=None):
        try:
            return fn()
        except (WmiError, HardwareError, OSError, ValueError) as exc:
            log.debug("lecture impossible: %s", exc)
            return default

    def _wmi_sensor(self, sensor: int) -> int | None:
        return self._try(lambda: W.parse_sysinfo(self._wmi_get(W.GET_GAMING_SYS_INFO, W.sysinfo_query(sensor))))

    def _wmi_profile(self) -> int | None:
        return self._try(lambda: W.parse_misc(self._wmi_get(W.GET_GAMING_MISC_SETTING, W.MISC_PLATFORM_PROFILE)))

    def _wmi_supported_profiles(self) -> list[str] | None:
        return self._try(lambda: W.parse_supported_profiles(
            self._wmi_get(W.GET_GAMING_MISC_SETTING, W.MISC_SUPPORTED_PROFILES)))

    def _profile_files(self):
        legacy = self.paths.sys("firmware", "acpi", "platform_profile")
        choices = self.paths.sys("firmware", "acpi", "platform_profile_choices")
        if legacy.exists() and choices.exists():
            return legacy, choices
        base = self.paths.sys("class", "platform-profile")
        if base.is_dir():
            for entry in sorted(base.iterdir()):
                if (read_text(entry / "name", "") or "").startswith("acer"):
                    return entry / "profile", entry / "choices"
        return None, None

    # -------------------------------------------------------------- detection
    def detect(self) -> dict:
        dmi = self.sensors.dmi()
        model = dmi.get("product_name", "")
        caps: dict = {
            "model": model or "inconnu",
            "vendor": dmi.get("sys_vendor", ""),
            "bios": dmi.get("bios_version", ""),
            "kernel": platform.release(),
            "model_ok": TARGET_MODEL.lower() in model.lower(),
            "transport": self._try(self.transport.describe, {"name": self.transport.name}),
        }
        caps["writes_allowed"] = caps["model_ok"] or bool(self.config.get("allow_unknown_model"))
        wmi = self.transport.supports(W.GAMING_GUID)
        caps["wmi"] = wmi

        profile_file, choices_file = self._profile_files()
        self._profile_map = {}
        if profile_file is not None:
            choices = (read_text(choices_file, "") or "").split()
            for name in PROFILES:
                for candidate in PROFILE_TO_SYSFS[name]:
                    if candidate in choices:
                        self._profile_map[name] = candidate
                        break
            caps.update(profile="sysfs", profile_choices=choices,
                        profiles=[n for n in PROFILES if n in self._profile_map])
        elif wmi and self._wmi_profile() is not None:
            supported = self._wmi_supported_profiles()
            profiles = [n for n in PROFILES if supported is None or n in supported]
            caps.update(profile="wmi", profile_choices=supported or [], profiles=profiles)
        else:
            caps.update(profile="none", profile_choices=[], profiles=[])

        if self.sensors.acer_fan_rpm():
            caps["fan_rpm"] = "hwmon"
        elif wmi and self._wmi_sensor(W.SENSOR_CPU_FAN) is not None:
            caps["fan_rpm"] = "wmi"
        else:
            caps["fan_rpm"] = "none"

        preference = self.config.get("fan_backend", "auto")
        pwm = self.sensors.acer_pwm()
        if preference in ("auto", "wmi") and wmi:
            caps["fan_control"] = "wmi"
        elif preference in ("auto", "hwmon") and pwm:
            caps["fan_control"] = "hwmon"
        else:
            caps["fan_control"] = "none"

        caps["keyboard"] = "wmi" if wmi else "none"
        caps["backlight_timeout"] = (
            "wmi" if self.transport.supports(W.APGE_GUID) and self._apge_backlight() is not None else "none")

        if self.sensors.battery_threshold_file() is not None:
            caps["battery_limit"] = "sysfs"
        elif self.transport.supports(W.BATTERY_GUID) and self._wmi_battery_health() is not None:
            caps["battery_limit"] = "wmi"
        else:
            caps["battery_limit"] = "none"

        caps["gpu_mux"] = self._gpu_mux() if wmi else None
        self.caps = caps
        return caps

    # --------------------------------------------------------------- profiles
    def get_profile(self) -> tuple[str | None, str | None]:
        backend = self.caps.get("profile")
        if backend == "sysfs":
            profile_file, _ = self._profile_files()
            raw = read_text(profile_file) if profile_file else None
            for name, value in self._profile_map.items():
                if value == raw:
                    return name, raw
            return SYSFS_TO_PROFILE.get(raw or "", raw), raw
        if backend == "wmi":
            value = self._wmi_profile()
            if value is None:
                return None, None
            name = W.OPMODE_NAMES.get(value, f"0x{value:02x}")
            return name, name
        return None, None

    def set_profile(self, name: str) -> None:
        if name not in PROFILES:
            raise HardwareError(f"Mode inconnu : {name}")
        self._check_writes()
        backend = self.caps.get("profile")
        if backend == "sysfs":
            value = self._profile_map.get(name)
            if not value:
                raise HardwareError(f"Le noyau ne propose pas ce mode (choix : {self.caps.get('profile_choices')})")
            profile_file, _ = self._profile_files()
            try:
                write_text(profile_file, value)
            except OSError as exc:
                raise HardwareError(f"Écriture de {profile_file} impossible : {exc}") from exc
        elif backend == "wmi":
            self._wmi_set(W.SET_GAMING_MISC_SETTING, W.misc_set(W.MISC_PLATFORM_PROFILE, W.OPMODE[name]))
        else:
            raise HardwareError("Changement de mode indisponible : ni platform_profile ni acpi_call.")

    # ------------------------------------------------------------------- fans
    def read_fan_rpm(self) -> dict:
        backend = self.caps.get("fan_rpm")
        if backend == "hwmon":
            return self.sensors.acer_fan_rpm()
        if backend == "wmi":
            return {"cpu": self._wmi_sensor(W.SENSOR_CPU_FAN), "gpu": self._wmi_sensor(W.SENSOR_GPU_FAN)}
        return {}

    def apply_fan(self, mode: str, cpu: int = 50, gpu: int = 50,
                  cpu_auto: bool = False, gpu_auto: bool = False) -> None:
        if mode not in W.FAN_MODES:
            raise HardwareError(f"Mode ventilateur inconnu : {mode}")
        self._check_writes()
        backend = self.caps.get("fan_control")
        if backend == "wmi":
            if mode == "custom":
                # Same sequence as NitroSense set_all_custom_fan_state().
                self._wmi_set(W.SET_GAMING_FAN_BEHAVIOR, W.fan_behavior(
                    W.FAN_MODE_AUTO if cpu_auto else W.FAN_MODE_CUSTOM,
                    W.FAN_MODE_AUTO if gpu_auto else W.FAN_MODE_CUSTOM))
                self._wmi_set(W.SET_GAMING_FAN_SPEED, W.fan_speed("cpu", cpu))
                self._wmi_set(W.SET_GAMING_FAN_SPEED, W.fan_speed("gpu", gpu))
            else:
                self._wmi_set(W.SET_GAMING_FAN_BEHAVIOR, W.fan_behavior_all(W.FAN_MODES[mode]))
        elif backend == "hwmon":
            self._apply_fan_hwmon(mode, {"cpu": (cpu, cpu_auto), "gpu": (gpu, gpu_auto)})
        else:
            raise HardwareError("Contrôle des ventilateurs indisponible : charger le module acpi_call (voir README).")

    def set_fan_speeds(self, cpu: int, gpu: int, cpu_auto: bool = False, gpu_auto: bool = False) -> None:
        """Slider update in Custom mode (NitroSense set_single_custom_fan_speed)."""
        self._check_writes()
        backend = self.caps.get("fan_control")
        if backend == "wmi":
            if not cpu_auto:
                self._wmi_set(W.SET_GAMING_FAN_SPEED, W.fan_speed("cpu", cpu))
            if not gpu_auto:
                self._wmi_set(W.SET_GAMING_FAN_SPEED, W.fan_speed("gpu", gpu))
        elif backend == "hwmon":
            self._apply_fan_hwmon("custom", {"cpu": (cpu, cpu_auto), "gpu": (gpu, gpu_auto)})
        else:
            raise HardwareError("Contrôle des ventilateurs indisponible.")

    def _apply_fan_hwmon(self, mode: str, fans: dict) -> None:
        pwm = self.sensors.acer_pwm()
        if not pwm:
            raise HardwareError("Le pilote acer-wmi n'expose pas de pwm (noyau trop ancien ?).")
        try:
            for fan, (pwm_file, enable_file) in pwm.items():
                percent, fan_auto = fans.get(fan, (50, False))
                if mode == "auto" or (mode == "custom" and fan_auto):
                    write_text(enable_file, "2")
                elif mode == "max":
                    try:
                        write_text(enable_file, "0")
                    except OSError:
                        write_text(enable_file, "1")
                        write_text(pwm_file, "255")
                else:
                    write_text(enable_file, "1")
                    write_text(pwm_file, str(round(max(0, min(100, percent)) * 255 / 100)))
        except OSError as exc:
            raise HardwareError(f"Écriture pwm impossible : {exc}") from exc

    # --------------------------------------------------------------- keyboard
    def _kb_backlight(self, payload: bytes) -> None:
        out = self.transport.call(W.GAMING_GUID, W.SET_GAMING_KB_BACKLIGHT, payload)
        raw = int.from_bytes(out[:8].ljust(8, b"\0"), "little")
        if not W.status_ok(raw):
            raise HardwareError(f"Le BIOS a refusé l'effet clavier (statut 0x{raw & 0xFF:02X})")

    def apply_keyboard(self, kb: dict) -> None:
        self._check_writes()
        if self.caps.get("keyboard") != "wmi":
            raise HardwareError("Éclairage clavier indisponible : charger le module acpi_call (voir README).")
        mode = kb.get("mode", "static")
        brightness = int(kb.get("brightness", 100))
        if mode == "off":
            self._kb_backlight(W.kb_backlight_payload("static", brightness=0))
            return
        if mode == "static":
            zones = kb["zones"][:W.KB_ZONES]
            self._kb_backlight(W.kb_backlight_payload("static", brightness=brightness))
            for index, zone in enumerate(zones):
                color = zone["color"] if zone.get("on", True) else "#000000"
                self._wmi_set(W.SET_GAMING_RGB_KB, W.rgb_zone(W.zone_bit(index), color))
            try:
                self._wmi_set(W.SET_GAMING_LED_BEHAVIOR, W.zones_enable([z.get("on", True) for z in zones]))
            except HardwareError as exc:  # zones are already blacked out above
                log.warning("SetGamingLEDBehavior refusé : %s", exc)
            return
        self._kb_backlight(W.kb_backlight_payload(
            kb.get("effect", "wave"), brightness=brightness, speed=int(kb.get("speed", 5)),
            direction=kb.get("direction", "right"), color=kb.get("color", "#FFFFFF")))

    # ------------------------------------------------------ backlight timeout
    def _apge_backlight(self) -> dict | None:
        return self._try(lambda: W.parse_apge_kb_backlight(self.transport.call_int(
            W.APGE_GUID, W.APGE_GET_FUNCTION, W.apge_kb_backlight_query(self._hotkey()), in_size=4)))

    def get_backlight_timeout(self) -> bool | None:
        if self.caps.get("backlight_timeout") != "wmi":
            return None
        info = self._apge_backlight()
        return info["timeout_enabled"] if info else None

    def set_backlight_timeout(self, enabled: bool) -> None:
        self._check_writes()
        info = self._apge_backlight()
        if info is None:
            raise HardwareError("Lecture du rétroéclairage impossible (APGeAction).")
        raw = self.transport.call_int(W.APGE_GUID, W.APGE_SET_FUNCTION,
                                      W.apge_kb_backlight_set(info["brightness"], enabled, self._hotkey()))
        if not W.status_ok(raw):
            raise HardwareError(f"Le BIOS a refusé le délai de rétroéclairage (statut 0x{raw & 0xFF:02X})")

    # ---------------------------------------------------------- battery limit
    def _wmi_battery_health(self) -> dict | None:
        return self._try(lambda: W.parse_battery_health(self.transport.call(
            W.BATTERY_GUID, W.BATTERY_GET_HEALTH_STATUS, W.battery_health_query())))

    def get_battery_limit(self) -> bool | None:
        backend = self.caps.get("battery_limit")
        if backend == "sysfs":
            value = read_int(self.sensors.battery_threshold_file())
            return None if value is None else value <= 80
        if backend == "wmi":
            info = self._wmi_battery_health()
            return info["health_mode"] if info else None
        return None

    def set_battery_limit(self, enabled: bool) -> None:
        self._check_writes()
        backend = self.caps.get("battery_limit")
        if backend == "sysfs":
            try:
                write_text(self.sensors.battery_threshold_file(), "80" if enabled else "100")
            except OSError as exc:
                raise HardwareError(f"Écriture du seuil de charge impossible : {exc}") from exc
            return
        if backend == "wmi":
            self.transport.call(W.BATTERY_GUID, W.BATTERY_SET_HEALTH_CONTROL, W.battery_health_set(enabled))
            if self.get_battery_limit() != enabled:
                raise HardwareError("La limite de charge n'a pas été prise en compte (format à vérifier).")
            return
        raise HardwareError("Limite de charge indisponible.")

    # ------------------------------------------------------------- GPU (MUX)
    def _gpu_mux(self) -> dict | None:
        support = self._try(lambda: W.parse_misc(self._wmi_get(W.GET_GAMING_MISC_SETTING, W.MISC_GPU_MODE_SUPPORT)))
        if support is None:
            return None
        mode = self._try(lambda: W.parse_misc(self._wmi_get(W.GET_GAMING_MISC_SETTING, W.MISC_GPU_MODE)))
        return {"supported": support == 3, "value": mode,
                "mode": W.GPU_MUX_NAMES.get(mode, "inconnu") if mode is not None else "inconnu"}

    # ---------------------------------------------------------------- sensors
    def gpu_temp_cheap(self) -> float | None:
        """GPU temperature without waking the dGPU (EC sensor or acer hwmon)."""
        temp = self.sensors.acer_temps().get("gpu")
        if temp is None and self.caps.get("wmi") and self.sensors.dgpu_state() == "active":
            value = self._wmi_sensor(W.SENSOR_GPU_TEMP)
            temp = float(value) if value else None
        return temp

    def read_sensors(self) -> dict:
        cpu_temp = self.sensors.cpu_temp()
        if cpu_temp is None and self.caps.get("wmi"):
            value = self._wmi_sensor(W.SENSOR_CPU_TEMP)
            cpu_temp = float(value) if value else None
        gpu = self.sensors.gpu()
        if gpu["temp"] is None and gpu["state"] == "active" and self.caps.get("wmi"):
            value = self._wmi_sensor(W.SENSOR_GPU_TEMP)
            gpu["temp"] = float(value) if value else None
        power = self.sensors.power()
        return {
            "cpu": {"temp": cpu_temp, "load": self.sensors.cpu_load(), "mhz": self.sensors.cpu_mhz()},
            "gpu": gpu,
            "rpm": self.read_fan_rpm(),
            "ac": power["ac"],
            "battery": power["battery"],
        }

    # ------------------------------------------------------------ diagnostics
    def raw_call(self, guid: str, method: int, payload: bytes) -> bytes:
        return self.transport.call(guid, method, payload)

    def selftest(self) -> dict:
        """Read-only checks of every interface, for testing on a new kernel."""
        caps = self.detect()
        checks: dict = {}

        def attempt(name, fn):
            try:
                checks[name] = fn()
            except Exception as exc:  # noqa: BLE001 - report everything
                checks[name] = f"ERREUR : {exc}"

        def get_hex(method, value):
            raw = self._wmi_get(method, value)
            return f"0x{raw:016X}"

        if caps.get("wmi"):
            for label, sensor in (("cpu_temp", W.SENSOR_CPU_TEMP), ("cpu_fan", W.SENSOR_CPU_FAN),
                                  ("system_temp", W.SENSOR_SYSTEM_TEMP), ("gpu_fan", W.SENSOR_GPU_FAN),
                                  ("gpu_temp", W.SENSOR_GPU_TEMP)):
                attempt(f"wmi.sysinfo.{label}", lambda s=sensor: get_hex(W.GET_GAMING_SYS_INFO, W.sysinfo_query(s)))
            for label, index in (("platform_profile", W.MISC_PLATFORM_PROFILE),
                                 ("supported_profiles", W.MISC_SUPPORTED_PROFILES),
                                 ("gpu_mode", W.MISC_GPU_MODE), ("gpu_mode_support", W.MISC_GPU_MODE_SUPPORT)):
                attempt(f"wmi.misc.{label}", lambda i=index: get_hex(W.GET_GAMING_MISC_SETTING, i))
            attempt("wmi.fan_behavior(0x09) [non vérifié]", lambda: get_hex(W.GET_GAMING_FAN_BEHAVIOR, 0x0009))
            attempt("wmi.fan_speed.cpu [non vérifié]", lambda: get_hex(W.GET_GAMING_FAN_SPEED, W.fan_speed_query("cpu")))
            attempt("wmi.fan_speed.gpu [non vérifié]", lambda: get_hex(W.GET_GAMING_FAN_SPEED, W.fan_speed_query("gpu")))
        if self.transport.supports(W.APGE_GUID):
            attempt("wmi.apge.kb_backlight", lambda: f"0x{self.transport.call_int(W.APGE_GUID, W.APGE_GET_FUNCTION, W.apge_kb_backlight_query(self._hotkey()), in_size=4):016X}")
        if self.transport.supports(W.BATTERY_GUID):
            attempt("wmi.battery.health", lambda: self.transport.call(
                W.BATTERY_GUID, W.BATTERY_GET_HEALTH_STATUS, W.battery_health_query()).hex(" "))
        profile_file, choices_file = self._profile_files()
        checks["sysfs.platform_profile"] = read_text(profile_file) if profile_file else "absent"
        checks["sysfs.platform_profile_choices"] = read_text(choices_file) if choices_file else "absent"
        hw = self.sensors.acer_hwmon()
        checks["sysfs.acer_hwmon"] = sorted(p.name for p in hw.iterdir()) if hw else "absent"
        checks["sysfs.acer_fan_rpm"] = self.sensors.acer_fan_rpm()
        checks["sysfs.acer_temps"] = self.sensors.acer_temps()
        checks["sysfs.acer_pwm"] = sorted(self.sensors.acer_pwm())
        checks["sysfs.cpu_temp"] = self.sensors.cpu_temp()
        checks["sysfs.dgpu"] = self.sensors.dgpu_state()
        checks["sysfs.power"] = self.sensors.power()
        threshold = self.sensors.battery_threshold_file()
        checks["sysfs.charge_control_end_threshold"] = str(threshold) if threshold else "absent"
        return {"caps": caps, "checks": checks}
