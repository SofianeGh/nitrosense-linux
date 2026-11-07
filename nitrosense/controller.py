"""Daemon core: validates commands, keeps the saved state and re-applies it
at boot and after suspend. The socket server (daemon.py) and the demo mode
both call Controller.handle()."""
from __future__ import annotations

import copy
import logging
import threading

from . import __version__
from . import wmi_codes as W
from .hardware import PROFILES, Hardware, HardwareError
from .paths import load_json, save_json_atomic
from .transport import WmiError

log = logging.getLogger("nitrosense")

DEFAULT_CONFIG = {
    "allowed_users": [],
    "fan_backend": "auto",
    "kb_hotkey_number": W.KB_HOTKEY_NUMBER,
    "allow_unknown_model": False,
    "performance_requires_ac": True,
    "min_battery_for_performance": 40,
    "thermal_guard": {"enabled": True, "cpu_trip": 92, "gpu_trip": 87, "cpu_clear": 80, "gpu_clear": 75},
}

DEFAULT_KEYBOARD = {
    "mode": "static",
    "brightness": 100,
    "zones": [{"on": True, "color": "#FF0000"} for _ in range(W.KB_ZONES)],
    "effect": "wave",
    "speed": 5,
    "direction": "right",
    "color": "#FFFFFF",
}

DEFAULT_STATE = {
    "profile": None,            # None: never chosen here, leave the firmware alone
    "fan": {"mode": "auto", "cpu": 50, "gpu": 50, "cpu_auto": False, "gpu_auto": False},
    "keyboard": None,
    "backlight_timeout": None,
    "battery_limit": None,
}

GUARD_COOL_TICKS = 15  # 15 ticks of 2 s below the clear thresholds


class UserError(Exception):
    """Invalid request; the message is shown to the user."""


def merge_config(user: dict | None) -> dict:
    config = copy.deepcopy(DEFAULT_CONFIG)
    for key, value in (user or {}).items():
        if isinstance(value, dict) and isinstance(config.get(key), dict):
            config[key].update(value)
        else:
            config[key] = value
    return config


def _as_bool(value, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise UserError(f"Paramètre {name} invalide (booléen attendu)")


def _as_percent(value, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise UserError(f"Paramètre {name} invalide (0-100 attendu)") from None
    if not 0 <= number <= 100:
        raise UserError(f"Paramètre {name} hors limites (0-100)")
    return number


def normalize_keyboard(data: dict) -> dict:
    if not isinstance(data, dict):
        raise UserError("Paramètre keyboard invalide")
    kb = copy.deepcopy(DEFAULT_KEYBOARD)
    mode = data.get("mode", kb["mode"])
    if mode not in ("static", "effect", "off"):
        raise UserError(f"Mode clavier inconnu : {mode}")
    kb["mode"] = mode
    kb["brightness"] = _as_percent(data.get("brightness", kb["brightness"]), "brightness")
    zones = data.get("zones", kb["zones"])
    if not isinstance(zones, list) or len(zones) != W.KB_ZONES:
        raise UserError("Il faut exactement 4 zones clavier")
    clean = []
    for zone in zones:
        if not isinstance(zone, dict):
            raise UserError("Zone clavier invalide")
        try:
            color = "#%02X%02X%02X" % W.parse_color(zone.get("color", "#FF0000"))
        except ValueError:
            raise UserError("Couleur de zone invalide") from None
        clean.append({"on": _as_bool(zone.get("on", True), "zone.on"), "color": color})
    kb["zones"] = clean
    effect = data.get("effect", kb["effect"])
    if effect not in W.KB_EFFECTS or effect == "static":
        raise UserError(f"Effet clavier inconnu : {effect}")
    kb["effect"] = effect
    try:
        speed = int(data.get("speed", kb["speed"]))
    except (TypeError, ValueError):
        raise UserError("Vitesse invalide") from None
    if not 1 <= speed <= 9:
        raise UserError("Vitesse hors limites (1-9)")
    kb["speed"] = speed
    direction = data.get("direction", kb["direction"])
    if direction not in W.KB_DIRECTIONS:
        raise UserError(f"Direction inconnue : {direction}")
    kb["direction"] = direction
    try:
        kb["color"] = "#%02X%02X%02X" % W.parse_color(data.get("color", kb["color"]))
    except ValueError:
        raise UserError("Couleur d'effet invalide") from None
    return kb


class Controller:
    def __init__(self, hw: Hardware, state_path, config: dict | None = None, on_status=None):
        self.hw = hw
        self.state_path = state_path
        self.config = merge_config(config)
        self.on_status = on_status
        self.lock = threading.RLock()
        saved = load_json(state_path, {}) if state_path else {}
        self.state = copy.deepcopy(DEFAULT_STATE)
        for key in self.state:
            if key in saved:
                self.state[key] = saved[key]
        self.state["fan"] = {**DEFAULT_STATE["fan"], **(self.state.get("fan") or {})}
        self.guard_active = False
        self._cool_ticks = 0
        self.cached_backlight = None
        self.cached_battery = None
        self.last_error: str | None = None

    # ------------------------------------------------------------- lifecycle
    def startup(self) -> list[str]:
        with self.lock:
            caps = self.hw.detect()
            log.info("Modèle %s, BIOS %s, noyau %s", caps.get("model"), caps.get("bios"), caps.get("kernel"))
            log.info("Backends: mode=%s rpm=%s ventilos=%s clavier=%s batterie=%s délai_clavier=%s",
                     caps.get("profile"), caps.get("fan_rpm"), caps.get("fan_control"),
                     caps.get("keyboard"), caps.get("battery_limit"), caps.get("backlight_timeout"))
            self._refresh_cache()
            return self.reapply("démarrage")

    def shutdown(self) -> None:
        """Daemon stop: never leave the fans on a manual setting."""
        with self.lock:
            if self.state["fan"]["mode"] != "auto" and self.hw.caps.get("fan_control", "none") != "none":
                try:
                    self.hw.apply_fan("auto")
                    log.info("Arrêt du service : ventilateurs remis en Auto")
                except (HardwareError, WmiError) as exc:
                    log.warning("Impossible de remettre les ventilateurs en Auto : %s", exc)

    def _save(self) -> None:
        if self.state_path:
            try:
                save_json_atomic(self.state_path, self.state)
            except OSError as exc:
                log.warning("Sauvegarde de l'état impossible : %s", exc)

    def _refresh_cache(self) -> None:
        self.cached_backlight = self.hw.get_backlight_timeout()
        self.cached_battery = self.hw.get_battery_limit()

    def _power_rule(self, target: str, power: dict | None = None) -> str | None:
        """NitroSense only allows Performance on AC with >= 40% battery."""
        if target != "performance" or not self.config.get("performance_requires_ac", True):
            return None
        power = power or self.hw.sensors.power()
        if power.get("ac") is False:
            return "Le mode Performance n'est possible que lorsque l'adaptateur secteur est branché."
        battery = power.get("battery")
        minimum = int(self.config.get("min_battery_for_performance", 40))
        if battery is not None and battery < minimum:
            return f"Le mode Performance demande au moins {minimum} % de batterie."
        return None

    def reapply(self, reason: str = "") -> list[str]:
        """Apply the saved settings (boot, resume, or on request)."""
        errors: list[str] = []
        caps = self.hw.caps
        state = self.state

        def run(label, fn):
            try:
                fn()
            except (HardwareError, WmiError, OSError) as exc:
                errors.append(f"{label} : {exc}")
                log.warning("Réapplication %s impossible : %s", label, exc)

        if state["profile"] and caps.get("profile", "none") != "none":
            current, _ = self.hw.get_profile()
            if current != state["profile"]:
                blocked = self._power_rule(state["profile"])
                if blocked:
                    log.info("Mode %s non réappliqué : %s", state["profile"], blocked)
                else:
                    run("mode", lambda: self.hw.set_profile(state["profile"]))
        fan = state["fan"]
        if fan["mode"] != "auto" and caps.get("fan_control", "none") != "none":
            current, _ = self.hw.get_profile()
            if current == "quiet":
                fan["mode"] = "auto"
            else:
                run("ventilateurs", lambda: self.hw.apply_fan(**fan))
        if state["keyboard"] and caps.get("keyboard", "none") != "none":
            run("clavier", lambda: self.hw.apply_keyboard(state["keyboard"]))
        if state["backlight_timeout"] is not None and caps.get("backlight_timeout", "none") != "none":
            run("délai clavier", lambda: self.hw.set_backlight_timeout(state["backlight_timeout"]))
        if state["battery_limit"] is not None and caps.get("battery_limit", "none") != "none":
            run("limite batterie", lambda: self.hw.set_battery_limit(state["battery_limit"]))
        self._save()
        self._refresh_cache()
        if reason:
            log.info("Réglages réappliqués (%s)%s", reason, f" avec {len(errors)} erreur(s)" if errors else "")
        return errors

    # --------------------------------------------------------- thermal guard
    def tick(self) -> None:
        """Called every 2 s by the daemon: NitroSense rules + thermal safety net."""
        with self.lock:
            if self.hw.caps.get("fan_control", "none") == "none":
                return
            fan = self.state["fan"]
            try:
                profile, _ = self.hw.get_profile()
                if profile == "quiet" and fan["mode"] != "auto":
                    # NitroSense: "Fan control disabled in Quiet mode".
                    self.hw.apply_fan("auto")
                    fan["mode"] = "auto"
                    self.guard_active = False
                    self._save()
                    log.info("Mode Silencieux actif : ventilateurs remis en Auto")
                    return
                guard = self.config.get("thermal_guard", {})
                manual_low = fan["mode"] == "custom" and any(
                    not fan[f"{f}_auto"] and fan[f] < 100 for f in W.FANS)
                if not guard.get("enabled", True) or not manual_low:
                    self.guard_active = False
                    return
                cpu = self.hw.sensors.cpu_temp()
                gpu = self.hw.gpu_temp_cheap()
                hot = (cpu is not None and cpu >= guard.get("cpu_trip", 92)) or \
                      (gpu is not None and gpu >= guard.get("gpu_trip", 87))
                if hot and not self.guard_active:
                    self.hw.apply_fan("auto")
                    self.guard_active = True
                    self._cool_ticks = 0
                    log.warning("Sécurité thermique : CPU %s °C / GPU %s °C, ventilateurs en Auto", cpu, gpu)
                elif self.guard_active:
                    cool = (cpu is None or cpu < guard.get("cpu_clear", 80)) and \
                           (gpu is None or gpu < guard.get("gpu_clear", 75))
                    self._cool_ticks = self._cool_ticks + 1 if cool else 0
                    if self._cool_ticks >= GUARD_COOL_TICKS:
                        self.hw.apply_fan(**fan)
                        self.guard_active = False
                        log.info("Températures revenues à la normale : réglage Perso rétabli")
            except (HardwareError, WmiError, OSError) as exc:
                self.last_error = str(exc)
                log.warning("tick : %s", exc)

    # -------------------------------------------------------------- dispatch
    def handle(self, request: dict, uid: int = 0) -> dict:
        if not isinstance(request, dict):
            return {"ok": False, "error": "Requête invalide"}
        cmd = request.get("cmd")
        handler = getattr(self, f"cmd_{cmd}", None) if isinstance(cmd, str) and cmd.isidentifier() else None
        if handler is None:
            return {"ok": False, "error": f"Commande inconnue : {cmd!r}"}
        try:
            with self.lock:
                result = handler(request, uid) or {}
            return {"ok": True, **result}
        except (UserError, HardwareError) as exc:
            self.last_error = str(exc)
            return {"ok": False, "error": str(exc)}
        except WmiError as exc:
            self.last_error = str(exc)
            return {"ok": False, "error": f"Erreur WMI : {exc}"}
        except Exception as exc:  # noqa: BLE001 - keep the daemon alive
            log.exception("Erreur interne sur %s", cmd)
            return {"ok": False, "error": f"Erreur interne : {exc}"}

    # -------------------------------------------------------------- commands
    def cmd_ping(self, req, uid):
        return {"version": __version__}

    def cmd_capabilities(self, req, uid):
        return {"caps": self.hw.caps}

    def cmd_redetect(self, req, uid):
        caps = self.hw.detect()
        self._refresh_cache()
        return {"caps": caps}

    def cmd_status(self, req, uid):
        if self.on_status:
            self.on_status()
        sensors = self.hw.read_sensors()
        profile, raw = self.hw.get_profile()
        power = {"ac": sensors["ac"], "battery": sensors["battery"]}
        return {
            "version": __version__,
            "caps": self.hw.caps,
            "profile": profile,
            "profile_raw": raw,
            "performance_blocked": self._power_rule("performance", power),
            "fan": dict(self.state["fan"]),
            "fan_guard": self.guard_active,
            "fan_control_locked": profile == "quiet",
            "rpm": sensors["rpm"],
            "cpu": sensors["cpu"],
            "gpu": sensors["gpu"],
            "ac": sensors["ac"],
            "battery": sensors["battery"],
            "keyboard": self.state["keyboard"] or copy.deepcopy(DEFAULT_KEYBOARD),
            "keyboard_set": self.state["keyboard"] is not None,
            "backlight_timeout": self.cached_backlight,
            "battery_limit": self.cached_battery,
            "gpu_mux": self.hw.caps.get("gpu_mux"),
            "last_error": self.last_error,
        }

    def cmd_set_profile(self, req, uid):
        name = req.get("name")
        if name not in PROFILES:
            raise UserError(f"Mode inconnu : {name!r} (quiet, default, performance)")
        blocked = self._power_rule(name)
        if blocked:
            raise UserError(blocked)
        self.hw.set_profile(name)
        self.state["profile"] = name
        if name == "quiet" and self.state["fan"]["mode"] != "auto" \
                and self.hw.caps.get("fan_control", "none") != "none":
            self.hw.apply_fan("auto")
            self.state["fan"]["mode"] = "auto"
            self.guard_active = False
        self._save()
        return {"profile": name}

    def _fan_from_request(self, req) -> dict:
        fan = dict(self.state["fan"])
        if "mode" in req:
            if req["mode"] not in W.FAN_MODES:
                raise UserError(f"Mode ventilateur inconnu : {req['mode']!r}")
            fan["mode"] = req["mode"]
        for key in ("cpu", "gpu"):
            if key in req:
                fan[key] = _as_percent(req[key], key)
        for key in ("cpu_auto", "gpu_auto"):
            if key in req:
                fan[key] = _as_bool(req[key], key)
        return fan

    def _check_fan_allowed(self) -> None:
        profile, _ = self.hw.get_profile()
        if profile == "quiet":
            raise UserError("Contrôle du ventilateur désactivé en mode Silencieux.")

    def cmd_set_fan(self, req, uid):
        fan = self._fan_from_request(req)
        if fan["mode"] != "auto":
            self._check_fan_allowed()
        self.hw.apply_fan(**fan)
        self.state["fan"] = fan
        self.guard_active = False
        self._save()
        return {"fan": fan}

    def cmd_set_fan_speed(self, req, uid):
        fan = self._fan_from_request({k: v for k, v in req.items() if k != "mode"})
        if self.state["fan"]["mode"] != "custom":
            raise UserError("Passer d'abord en mode Perso")
        self._check_fan_allowed()
        if self.guard_active:
            self.hw.apply_fan(**fan)
            self.guard_active = False
        else:
            self.hw.set_fan_speeds(fan["cpu"], fan["gpu"], fan["cpu_auto"], fan["gpu_auto"])
        self.state["fan"] = fan
        self._save()
        return {"fan": fan}

    def cmd_set_keyboard(self, req, uid):
        kb = normalize_keyboard(req.get("keyboard"))
        self.hw.apply_keyboard(kb)
        self.state["keyboard"] = kb
        self._save()
        return {"keyboard": kb}

    def cmd_set_backlight_timeout(self, req, uid):
        enabled = _as_bool(req.get("enabled"), "enabled")
        if self.hw.caps.get("backlight_timeout", "none") == "none":
            raise UserError("Réglage du délai de rétroéclairage indisponible")
        self.hw.set_backlight_timeout(enabled)
        self.state["backlight_timeout"] = enabled
        self._save()
        self.cached_backlight = self.hw.get_backlight_timeout()
        return {"backlight_timeout": self.cached_backlight}

    def cmd_set_battery_limit(self, req, uid):
        enabled = _as_bool(req.get("enabled"), "enabled")
        if self.hw.caps.get("battery_limit", "none") == "none":
            raise UserError("Limite de charge indisponible sur ce noyau")
        self.hw.set_battery_limit(enabled)
        self.state["battery_limit"] = enabled
        self._save()
        self.cached_battery = self.hw.get_battery_limit()
        return {"battery_limit": self.cached_battery}

    def cmd_reapply(self, req, uid):
        return {"errors": self.reapply("demande")}

    def cmd_selftest(self, req, uid):
        report = self.hw.selftest()
        self._refresh_cache()
        return {"report": report}

    def cmd_raw_wmi(self, req, uid):
        if uid != 0:
            raise UserError("raw_wmi est réservé à root (sudo nitrosense-cli raw ...)")
        guid = W.GUID_ALIASES.get(str(req.get("guid", "")).lower(), str(req.get("guid", "")).upper())
        try:
            method = int(req.get("method"))
            payload = bytes.fromhex(str(req.get("input", "")).replace(" ", ""))
        except (TypeError, ValueError):
            raise UserError("raw_wmi : method (entier) et input (hexadécimal) requis") from None
        out = self.hw.raw_call(guid, method, payload)
        value = int.from_bytes(out[:8].ljust(8, b"\0"), "little")
        log.warning("raw_wmi %s méthode %d entrée %s -> %s", guid, method, payload.hex(), out.hex())
        return {"output_hex": out.hex(" "), "output_u64": f"0x{value:016X}"}
