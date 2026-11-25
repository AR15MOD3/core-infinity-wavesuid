"""Installed inside each private gsuid_core package by native_registration."""
from pathlib import Path

CORE_ROOT = Path(__file__).resolve().parent


def plugin_parts(filename):
    if not filename:
        return ()
    try:
        parts = Path(filename).resolve().relative_to(CORE_ROOT).parts
    except (OSError, RuntimeError, ValueError):
        return ()
    if len(parts) < 2 or parts[0] not in ("plugins", "buildin_plugins"):
        return ()
    return parts
