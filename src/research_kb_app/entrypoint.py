from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any


class RuntimeCompatibilityError(RuntimeError):
    pass


def main(argv: Sequence[str] | None = None) -> int:
    command = list(sys.argv[1:] if argv is None else argv)
    try:
        compatibility = _verify_runtime(command)
    except RuntimeCompatibilityError as error:
        print(f"Research KB App refused to start: {error}", file=sys.stderr)
        return 2

    try:
        launcher_start = _load_launcher_start()
    except (ImportError, ModuleNotFoundError) as error:
        # Compatibility verification passed; only the App's own feature modules
        # are incomplete. Do not report this as a Core incompatibility.
        print("Research KB App refused to start: App installation is incomplete", file=sys.stderr)
        return 2
    return launcher_start(command, compatibility)


def _verify_runtime(argv: Sequence[str]) -> Any:
    compatibility_path = _compatibility_path(argv)
    try:
        from research_kb_app.compatibility import (
            CompatibilityError,
            load_compatibility,
            verify_installed_core,
        )
    except ImportError as error:
        raise RuntimeCompatibilityError(
            "Installed Research KB Core is incompatible with this App build"
        ) from error

    try:
        compatibility = load_compatibility(compatibility_path)
        verify_installed_core(compatibility)
    except (CompatibilityError, OSError, ValueError) as error:
        raise RuntimeCompatibilityError(str(error)) from error
    return compatibility


def _compatibility_path(argv: Sequence[str]) -> Path | None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--compatibility", type=Path)
    args, _unknown = parser.parse_known_args(argv)
    return args.compatibility


def _load_launcher_start() -> Callable[..., int]:
    from research_kb_app.launcher import _start_after_compatibility

    return _start_after_compatibility


__all__ = ["RuntimeCompatibilityError", "main"]
