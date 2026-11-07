"""Clients for the daemon: over its Unix socket, or in-process (demo/tests)."""
from __future__ import annotations

import json
import socket

SOCKET_PATH = "/run/nitrosense/nitrosense.sock"


class ClientError(RuntimeError):
    pass


class DaemonClient:
    def __init__(self, socket_path: str = SOCKET_PATH, timeout: float = 8.0):
        self.socket_path = socket_path
        self.timeout = timeout

    def request(self, cmd: str, **params) -> dict:
        family = getattr(socket, "AF_UNIX", None)
        if family is None:
            raise ClientError("Sockets Unix indisponibles sur ce système (utiliser --demo)")
        message = (json.dumps({"cmd": cmd, **params}) + "\n").encode("utf-8")
        try:
            with socket.socket(family, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect(self.socket_path)
                sock.sendall(message)
                chunks = []
                while True:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                    if chunk.endswith(b"\n"):
                        break
        except FileNotFoundError:
            raise ClientError("Service nitrosensed absent (sudo systemctl start nitrosensed)") from None
        except PermissionError:
            raise ClientError(f"Accès refusé à {self.socket_path}") from None
        except OSError as exc:
            raise ClientError(f"Service nitrosensed injoignable : {exc}") from None
        try:
            return json.loads(b"".join(chunks).decode("utf-8"))
        except ValueError:
            raise ClientError("Réponse illisible du service") from None


class LocalClient:
    """Calls a Controller directly (demo mode, tests)."""

    def __init__(self, controller, uid: int = 0):
        self.controller = controller
        self.uid = uid

    def request(self, cmd: str, **params) -> dict:
        return self.controller.handle({"cmd": cmd, **params}, self.uid)
