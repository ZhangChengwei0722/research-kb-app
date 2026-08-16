from __future__ import annotations

from pathlib import Path

import research_kb_app.runtime as runtime_module
from research_kb_app.external_reader import ExternalReaderResult


class NoOpExternalReaderLauncher:
    def launch(self, path: Path) -> ExternalReaderResult:
        source = Path(path)
        if not source.is_absolute() or not source.is_file():
            raise RuntimeError("E2E source review did not receive a validated absolute file")
        return ExternalReaderResult("test-noop")


runtime_module.ExternalReaderLauncher = NoOpExternalReaderLauncher

from research_kb_app.launcher import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
