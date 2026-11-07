"""Filesystem helpers. Every system path goes through Paths so tests and demo
mode can point the code at a fake /sys and /proc tree."""
from __future__ import annotations

import json
import os
from pathlib import Path


class Paths:
    def __init__(self, root: str | os.PathLike = "/"):
        self.root = Path(root)

    def sys(self, *parts: str) -> Path:
        return self.root.joinpath("sys", *parts)

    def proc(self, *parts: str) -> Path:
        return self.root.joinpath("proc", *parts)

    def etc(self, *parts: str) -> Path:
        return self.root.joinpath("etc", *parts)

    def var(self, *parts: str) -> Path:
        return self.root.joinpath("var", *parts)


def read_text(path, default: str | None = None) -> str | None:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().strip()
    except OSError:
        return default


def read_int(path, default: int | None = None) -> int | None:
    text = read_text(path)
    if text is None:
        return default
    try:
        return int(text, 0) if text.startswith("0x") else int(text)
    except ValueError:
        return default


def write_text(path, value: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(value)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def save_json_atomic(path, data) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")
    os.replace(tmp, path)
