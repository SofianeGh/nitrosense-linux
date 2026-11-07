"""nitrosense-cli: control and diagnostics from a terminal.

Examples:
    nitrosense-cli status
    nitrosense-cli selftest                (read-only checks of every interface)
    nitrosense-cli profile performance
    nitrosense-cli fan custom --cpu 60 --gpu 70
    nitrosense-cli kb static --zones FF0000,00FF00,0000FF,FFFFFF
    nitrosense-cli kb effect wave --speed 5 --direction left
    sudo nitrosense-cli raw gaming 5 01020000    (GetGamingSysInfo: CPU fan RPM)
"""
from __future__ import annotations

import argparse
import copy
import json
import sys

from .client import SOCKET_PATH, ClientError, DaemonClient, LocalClient

PROFILE_LABELS = {"quiet": "Silencieux", "default": "Par défaut", "performance": "Performance",
                  "turbo": "Turbo", "eco": "Éco"}
FAN_LABELS = {"auto": "Auto", "max": "Max", "custom": "Perso"}
EFFECT_LABELS = {"breathing": "Respiration", "wave": "Onde", "shifting": "Déplacement",
                 "zoom": "Zoom", "neon": "Néon"}


def _fmt(value, unit: str = "", digits: int = 0) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}{unit}"


def _onoff(value) -> str:
    return {True: "activé", False: "désactivé"}.get(value, "indisponible")


def print_status(st: dict) -> None:
    caps = st.get("caps", {})
    print(f"Modèle       : {caps.get('model')} (BIOS {caps.get('bios')}, noyau {caps.get('kernel')})")
    profile = st.get("profile")
    print(f"Mode         : {PROFILE_LABELS.get(profile, profile or '—')} [{st.get('profile_raw')}] "
          f"via {caps.get('profile')}")
    fan = st.get("fan", {})
    text = FAN_LABELS.get(fan.get("mode"), fan.get("mode"))
    if fan.get("mode") == "custom":
        cpu = "auto" if fan.get("cpu_auto") else f"{fan.get('cpu')} %"
        gpu = "auto" if fan.get("gpu_auto") else f"{fan.get('gpu')} %"
        text += f" (CPU {cpu}, GPU {gpu})"
    if st.get("fan_guard"):
        text += " — SÉCURITÉ THERMIQUE ACTIVE"
    print(f"Ventilateurs : {text} via {caps.get('fan_control')}")
    rpm = st.get("rpm") or {}
    print(f"Régime       : CPU {rpm.get('cpu', '—')} tr/min, GPU {rpm.get('gpu', '—')} tr/min "
          f"(via {caps.get('fan_rpm')})")
    cpu = st.get("cpu", {})
    print(f"CPU          : {_fmt(cpu.get('temp'), ' °C')}, charge {_fmt(cpu.get('load'), ' %')}, "
          f"{_fmt(cpu.get('mhz'), ' MHz')}")
    gpu = st.get("gpu", {})
    if gpu.get("state") == "suspended":
        print("GPU          : GPU distinct inactif (en veille)")
    else:
        print(f"GPU          : {_fmt(gpu.get('temp'), ' °C')}, charge {_fmt(gpu.get('load'), ' %')}, "
              f"{_fmt(gpu.get('mhz'), ' MHz')}")
    ac = st.get("ac")
    source = "secteur" if ac else ("batterie" if ac is False else "?")
    print(f"Alimentation : {source}, batterie {st.get('battery', '—')} %")
    kb = st.get("keyboard", {})
    if kb.get("mode") == "static":
        zones = ", ".join(z["color"] if z["on"] else "éteinte" for z in kb.get("zones", []))
        kb_text = f"statique ({zones}), luminosité {kb.get('brightness')} %"
    elif kb.get("mode") == "effect":
        kb_text = (f"{EFFECT_LABELS.get(kb.get('effect'), kb.get('effect'))}, vitesse {kb.get('speed')}, "
                   f"{kb.get('direction')}, {kb.get('color')}, luminosité {kb.get('brightness')} %")
    else:
        kb_text = "éteint"
    if not st.get("keyboard_set"):
        kb_text += " (jamais réglé ici)"
    print(f"Clavier      : {kb_text} via {caps.get('keyboard')}")
    print(f"Limite 80 %  : {_onoff(st.get('battery_limit'))} (via {caps.get('battery_limit')})")
    print(f"Clavier 30 s : {_onoff(st.get('backlight_timeout'))}")
    mux = st.get("gpu_mux")
    if mux:
        print(f"GPU (MUX)    : {mux.get('mode')} (valeur {mux.get('value')}, lecture seule)")
    if st.get("performance_blocked"):
        print(f"Note         : {st['performance_blocked']}")
    if st.get("last_error"):
        print(f"Dernière erreur : {st['last_error']}")


def make_client(args):
    if args.demo:
        from .demo import make_demo_controller
        controller, _world = make_demo_controller()
        return LocalClient(controller)
    return DaemonClient(args.socket)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nitrosense-cli", description="NitroSense Linux en ligne de commande")
    parser.add_argument("--socket", default=SOCKET_PATH)
    parser.add_argument("--demo", action="store_true", help="matériel simulé (sans service)")
    parser.add_argument("--json", action="store_true", help="réponse JSON brute")
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("status", "caps", "selftest", "reapply", "ping", "redetect"):
        sub.add_parser(name)
    p = sub.add_parser("profile", help="mode de performance")
    p.add_argument("name", choices=["quiet", "default", "performance", "turbo"])
    p = sub.add_parser("fan", help="ventilateurs")
    p.add_argument("mode", choices=["auto", "max", "custom"])
    p.add_argument("--cpu", type=int)
    p.add_argument("--gpu", type=int)
    p.add_argument("--cpu-auto", action="store_true")
    p.add_argument("--gpu-auto", action="store_true")
    p = sub.add_parser("kb", help="éclairage clavier")
    kb = p.add_subparsers(dest="kb_mode", required=True)
    s = kb.add_parser("static")
    s.add_argument("--color", help="même couleur pour les 4 zones, ex. FF0000")
    s.add_argument("--zones", help="4 couleurs séparées par des virgules")
    s.add_argument("--off", default="", help="zones éteintes (1-4), ex. 3,4")
    s.add_argument("--brightness", type=int)
    e = kb.add_parser("effect")
    e.add_argument("effect", choices=sorted(EFFECT_LABELS))
    e.add_argument("--speed", type=int)
    e.add_argument("--brightness", type=int)
    e.add_argument("--direction", choices=["right", "left"])
    e.add_argument("--color")
    kb.add_parser("off")
    p = sub.add_parser("backlight-timeout", help="éteindre le clavier après 30 s")
    p.add_argument("state", choices=["on", "off"])
    p = sub.add_parser("battery-limit", help="limiter la charge à 80 %%")
    p.add_argument("state", choices=["on", "off"])
    p = sub.add_parser("raw", help="appel WMI brut (root, débogage)")
    p.add_argument("guid", help="gaming, apge, battery ou GUID complet")
    p.add_argument("method", type=lambda text: int(text, 0))
    p.add_argument("input", help="octets d'entrée en hexadécimal, petit-boutiste")
    return parser


def _keyboard_request(args, current: dict) -> dict:
    kb = copy.deepcopy(current)
    if args.kb_mode == "off":
        kb["mode"] = "off"
        return kb
    if args.kb_mode == "static":
        kb["mode"] = "static"
        if args.zones:
            colors = [c.strip() for c in args.zones.split(",")]
            if len(colors) != 4:
                raise SystemExit("--zones attend 4 couleurs")
            for zone, color in zip(kb["zones"], colors):
                zone["color"] = color
        elif args.color:
            for zone in kb["zones"]:
                zone["color"] = args.color
        off = {int(x) for x in args.off.split(",") if x.strip()}
        for index, zone in enumerate(kb["zones"], start=1):
            zone["on"] = index not in off
        if args.brightness is not None:
            kb["brightness"] = args.brightness
        return kb
    kb["mode"] = "effect"
    kb["effect"] = args.effect
    for key in ("speed", "brightness", "direction", "color"):
        value = getattr(args, key)
        if value is not None:
            kb[key] = value
    return kb


def run(args, client) -> dict:
    command = args.command
    if command in ("status", "caps", "selftest", "reapply", "ping", "redetect"):
        return client.request({"caps": "capabilities"}.get(command, command))
    if command == "profile":
        return client.request("set_profile", name=args.name)
    if command == "fan":
        params = {"mode": args.mode}
        if args.cpu is not None:
            params["cpu"] = args.cpu
        if args.gpu is not None:
            params["gpu"] = args.gpu
        if args.mode == "custom":
            params["cpu_auto"] = args.cpu_auto
            params["gpu_auto"] = args.gpu_auto
        return client.request("set_fan", **params)
    if command == "kb":
        status = client.request("status")
        if not status.get("ok"):
            return status
        return client.request("set_keyboard", keyboard=_keyboard_request(args, status["keyboard"]))
    if command == "backlight-timeout":
        return client.request("set_backlight_timeout", enabled=args.state == "on")
    if command == "battery-limit":
        return client.request("set_battery_limit", enabled=args.state == "on")
    if command == "raw":
        return client.request("raw_wmi", guid=args.guid, method=args.method, input=args.input)
    raise SystemExit(f"commande inconnue : {command}")


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        client = make_client(args)
        response = run(args, client)
    except ClientError as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 2
    if args.json or args.command in ("caps", "selftest", "raw", "ping", "redetect"):
        print(json.dumps(response, indent=2, ensure_ascii=False))
    elif not response.get("ok"):
        pass
    elif args.command == "status":
        print_status(response)
    elif args.command == "reapply" and response.get("errors"):
        print("\n".join(response["errors"]))
    else:
        print("OK")
    if not response.get("ok"):
        print(f"Erreur : {response.get('error')}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
