from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from research_kb_app.errors import AppOperationError


WINDOWS_UPDF_CANDIDATES = (
    Path("C:/Program Files (x86)/UPDF/UPDF.exe"),
    Path("C:/Program Files/UPDF/UPDF.exe"),
)
MACOS_UPDF_APP = Path("/Applications/UPDF.app")


@dataclass(frozen=True, slots=True)
class ExternalReaderResult:
    reader: str
    page_targeting: str = "manual"


class ExternalReaderLauncher:
    def __init__(
        self,
        *,
        platform: str = sys.platform,
        path_exists: Callable[[Path], bool] = Path.is_file,
        process_launcher: Callable[..., Any] = subprocess.Popen,
        system_opener: Callable[[Path], Any] | None = None,
    ) -> None:
        self._platform = platform
        self._path_exists = path_exists
        self._process_launcher = process_launcher
        self._system_opener = system_opener

    def launch(self, path: Path) -> ExternalReaderResult:
        source = Path(path)
        if not source.is_absolute():
            raise _launch_error("The validated PDF path is not absolute")
        try:
            if self._platform == "win32":
                updf = next(
                    (candidate for candidate in WINDOWS_UPDF_CANDIDATES if self._path_exists(candidate)),
                    None,
                )
                if updf is not None:
                    self._spawn([str(updf), str(source)])
                    return ExternalReaderResult("updf")
                opener = self._system_opener or _windows_system_open
                opener(source)
                return ExternalReaderResult("system")
            if self._platform == "darwin":
                if self._path_exists(MACOS_UPDF_APP):
                    self._spawn(["open", "-a", "UPDF", str(source)])
                    return ExternalReaderResult("updf")
                self._spawn(["open", str(source)])
                return ExternalReaderResult("system")
            if self._platform.startswith("linux"):
                self._spawn(["xdg-open", str(source)])
                return ExternalReaderResult("system")
        except (OSError, subprocess.SubprocessError) as error:
            raise _launch_error("The local PDF reader could not be opened") from error
        raise _launch_error("No supported local PDF reader adapter is available")

    def _spawn(self, args: Sequence[str]) -> None:
        self._process_launcher(list(args), shell=False, close_fds=True)


def _windows_system_open(path: Path) -> None:
    startfile = getattr(os, "startfile", None)
    if not callable(startfile):
        raise OSError("Windows file association API is unavailable")
    startfile(str(path))


def _launch_error(message: str) -> AppOperationError:
    return AppOperationError("RKBAPP-PDF-READER", message, status_code=409)


__all__ = ["ExternalReaderLauncher", "ExternalReaderResult"]
