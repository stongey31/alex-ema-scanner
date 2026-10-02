"""Finds every scanner in the scanners/ package. A broken file never crashes anything."""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import traceback
from dataclasses import dataclass

from core.scanner_base import Scanner


@dataclass
class BrokenScanner:
    id: str
    error: str


def _sort_key(item):
    sid, obj = item
    return (0 if sid == "ma_bounce" else 1, getattr(obj, "order", 100), sid)


def discover_scanners(package: str = "scanners") -> list[tuple[str, object]]:
    found: list[tuple[str, object]] = []
    try:
        pkg = importlib.import_module(package)
        paths = list(pkg.__path__)
    except Exception:
        return [(package, BrokenScanner(package, traceback.format_exc(limit=3)))]

    for info in pkgutil.iter_modules(paths):
        name = info.name
        if name.startswith("_") or name.startswith("test"):
            continue
        try:
            module = importlib.import_module(f"{package}.{name}")
            classes = [
                c
                for _n, c in inspect.getmembers(module, inspect.isclass)
                if issubclass(c, Scanner) and c is not Scanner and c.__module__ == module.__name__
            ]
            if len(classes) != 1:
                raise ValueError(f"expected exactly one Scanner subclass in scanners/{name}.py, found {len(classes)}")
            instance = classes[0]()
            instance.id = name
            found.append((name, instance))
        except BaseException as e:  # noqa: BLE001 - includes SyntaxError; never crash
            if isinstance(e, (KeyboardInterrupt,)):
                raise
            found.append((name, BrokenScanner(name, f"{type(e).__name__}: {e}")))
    return sorted(found, key=_sort_key)
