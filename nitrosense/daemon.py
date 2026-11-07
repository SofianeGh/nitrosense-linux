"""nitrosensed: root service that owns the hardware.

The GUI and the CLI talk to it over a Unix socket (one JSON line per request).
Run by systemd (packaging/nitrosensed.service), or by hand for testing:
    sudo PYTHONPATH=. python3 -m nitrosense.daemon --verbose
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import socket
import struct
import sys
import threading
from pathlib import Path

from .client import SOCKET_PATH
from .controller import Controller, merge_config
from .hardware import Hardware
from .paths import Paths, load_json
from .transport import AcpiCallTransport

log = logging.getLogger("nitrosense")

CONFIG_PATH = "/etc/nitrosense/config.json"
STATE_PATH = "/var/lib/nitrosense/state.json"
ADMIN_GROUPS = ("sudo", "wheel", "admin")
MAX_REQUEST = 64 * 1024


def peer_uid(sock) -> int | None:
    try:
        creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", creds)
        return uid
    except (OSError, AttributeError):
        return None


class AccessPolicy:
    """root, the users listed in allowed_users, or (if the list is empty)
    members of an admin group (sudo/wheel/admin)."""

    def __init__(self, allowed_users):
        import grp
        import pwd
        self._pwd, self._grp = pwd, grp
        self.allowed: set[int] = set()
        for entry in allowed_users or []:
            try:
                self.allowed.add(int(entry) if str(entry).isdigit() else pwd.getpwnam(str(entry)).pw_uid)
            except KeyError:
                log.warning("Utilisateur inconnu dans allowed_users : %s", entry)
        self.open_to_admins = not allowed_users

    def permits(self, uid: int | None) -> bool:
        if uid is None:
            return False
        if uid == 0 or uid in self.allowed:
            return True
        if not self.open_to_admins:
            return False
        try:
            user = self._pwd.getpwuid(uid)
        except KeyError:
            return False
        for name in ADMIN_GROUPS:
            try:
                group = self._grp.getgrnam(name)
            except KeyError:
                continue
            if user.pw_name in group.gr_mem or user.pw_gid == group.gr_gid:
                return True
        return False


def make_server(controller: Controller, socket_path: str, policy: AccessPolicy):
    import socketserver

    class Handler(socketserver.StreamRequestHandler):
        def handle(self):
            uid = peer_uid(self.request)
            line = self.rfile.readline(MAX_REQUEST)
            if not line:
                return
            try:
                request = json.loads(line.decode("utf-8"))
            except ValueError:
                response = {"ok": False, "error": "JSON invalide"}
            else:
                if not policy.permits(uid):
                    response = {"ok": False, "error": (
                        f"Utilisateur {uid} non autorisé : l'ajouter à \"allowed_users\" "
                        "dans /etc/nitrosense/config.json puis redémarrer nitrosensed")}
                else:
                    response = controller.handle(request, uid if uid is not None else -1)
            self.wfile.write((json.dumps(response) + "\n").encode("utf-8"))

    class Server(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
        daemon_threads = True

    path = Path(socket_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() or path.is_symlink():
        path.unlink()
    server = Server(str(path), Handler)
    os.chmod(path, 0o666)  # access is checked per request with SO_PEERCRED
    return server


def serve(controller: Controller, socket_path: str, policy: AccessPolicy) -> None:
    path = Path(socket_path)
    server = make_server(controller, socket_path, policy)
    stop = threading.Event()

    def ticker():
        while not stop.wait(2.0):
            controller.tick()

    threading.Thread(target=ticker, name="nitrosense-tick", daemon=True).start()

    def on_signal(signum, _frame):
        log.info("Signal %s reçu, arrêt", signum)
        stop.set()
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    log.info("En écoute sur %s", path)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        stop.set()
        server.server_close()
        controller.shutdown()
        try:
            path.unlink()
        except OSError:
            pass


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="nitrosensed", description="Service NitroSense Linux")
    parser.add_argument("--root", default="/", help="racine du système (tests)")
    parser.add_argument("--socket", default=SOCKET_PATH)
    parser.add_argument("--config", default=CONFIG_PATH)
    parser.add_argument("--state", default=STATE_PATH)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(name)s: %(message)s", stream=sys.stderr)
    if hasattr(os, "geteuid") and os.geteuid() != 0:
        log.warning("nitrosensed ne tourne pas en root : les écritures matérielles vont échouer")

    config = merge_config(load_json(args.config, {}))
    paths = Paths(args.root)
    hardware = Hardware(paths, AcpiCallTransport(paths), config)
    controller = Controller(hardware, args.state, config)
    errors = controller.startup()
    for error in errors:
        log.warning("Au démarrage : %s", error)
    serve(controller, args.socket, AccessPolicy(config.get("allowed_users")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
