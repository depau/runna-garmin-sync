"""Tiny file-based state: one JSON blob per concern, written atomically."""

import json
import os
from pathlib import Path


class State:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)

    def path(self, name: str) -> Path:
        return self.dir / name

    def load(self, name: str, default=None):
        try:
            return json.loads((self.dir / name).read_text())
        except FileNotFoundError:
            return default

    def save(self, name: str, obj) -> None:
        p = self.dir / name
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(obj, indent=2, ensure_ascii=False))
        os.replace(tmp, p)
