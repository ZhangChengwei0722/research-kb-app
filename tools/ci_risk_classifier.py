"""Fail-closed risk classification for source and governance changes.

The classifier is intentionally conservative. Unknown paths, malformed change
records, renames, deletions, and high-impact policy/configuration paths resolve
to L3. A caller may raise the declared floor, but never lower the path-derived
classification.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import PurePosixPath

LEVELS = ("L0", "L1", "L2", "L3")
_LEVEL_RANK = {level: rank for rank, level in enumerate(LEVELS)}
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:/")


@dataclass(frozen=True)
class Change:
    path: str
    status: str = "M"
    old_path: str | None = None


@dataclass(frozen=True)
class RiskAssessment:
    level: str
    reasons: tuple[str, ...]
    paths: tuple[str, ...]
    fail_closed: bool

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


_HIGH_RISK_MARKERS = (
    ("security", "security"),
    ("storage", "storage"),
    ("schema", "schema"),
    ("contract", "contract"),
    ("package", "package"),
    ("workflow", "workflow"),
    ("source-policy", "source_policy"),
    ("source_policy", "source_policy"),
    ("public-source", "source_policy"),
    ("public_source", "source_policy"),
)
_RELEASE_GOVERNANCE_PATHS = frozenset(
    {
        "scripts/verify_release_artifacts.py",
        "tools/ci_risk_classifier.py",
        "tools/release_artifact_policy.py",
    }
)
_DOC_NAMES = {
    "agents.md",
    "changelog.md",
    "contributing.md",
    "license",
    "license.md",
    "readme.md",
    "support.md",
}
_DOC_SUFFIXES = (".md", ".markdown", ".rst", ".txt")


def classify_paths(
    paths: Iterable[str],
    *,
    status: str = "M",
    declared_level: str | None = None,
) -> RiskAssessment:
    """Classify a collection of paths using one status for every path."""

    return classify_changes(
        (Change(path=path, status=status) for path in paths),
        declared_level=declared_level,
    )


def classify_changes(
    changes: Iterable[Change | Mapping[str, object] | Sequence[object] | str],
    *,
    declared_level: str | None = None,
) -> RiskAssessment:
    """Return the highest risk represented by *changes*.

    Accepted input forms are :class:`Change`, a path string, a mapping with
    ``status``/``path``/``old_path`` keys, or a two-item ``(status, path)``
    sequence. Malformed records are deliberately treated as unknown L3 input.
    """

    normalized_changes = tuple(_coerce_change(item) for item in changes)
    if not normalized_changes:
        level = "L0"
        reasons: list[str] = ["no_changes"]
        paths: list[str] = []
    else:
        level = "L0"
        reasons = []
        paths = []
        for change in normalized_changes:
            normalized_path = _normalize_path(change.path)
            paths.append(normalized_path or str(change.path))
            path_level, path_reason = _classify_path(normalized_path)
            level = _higher_level(level, path_level)
            _add_reason(reasons, path_reason)

            status_level, status_reason = _classify_status(change)
            level = _higher_level(level, status_level)
            if status_reason is not None:
                _add_reason(reasons, status_reason)

    if declared_level is not None:
        requested = _normalize_level(declared_level)
        if requested is None:
            level = "L3"
            _add_reason(reasons, "unknown_declared_level")
        else:
            # The declared level is a floor. It cannot downgrade path-derived risk.
            level = _higher_level(level, requested)

    return RiskAssessment(
        level=level,
        reasons=tuple(reasons),
        paths=tuple(paths),
        fail_closed=level == "L3",
    )


def parse_name_status(lines: Iterable[str]) -> tuple[Change, ...]:
    """Parse tab-separated ``git diff --name-status`` style lines."""

    changes: list[Change] = []
    for line in lines:
        fields = line.rstrip("\r\n").split("\t")
        if not fields or not fields[0].strip():
            continue
        status = fields[0].strip()
        if status[:1].upper() in {"R", "C"} and len(fields) >= 3:
            changes.append(Change(path=fields[-1], status=status, old_path=fields[-2]))
        elif len(fields) >= 2:
            changes.append(Change(path=fields[1], status=status))
        else:
            changes.append(Change(path="", status="UNKNOWN"))
    return tuple(changes)


def _coerce_change(value: Change | Mapping[str, object] | Sequence[object] | str) -> Change:
    if isinstance(value, Change):
        return value
    if isinstance(value, str):
        return Change(path=value)
    if isinstance(value, Mapping):
        path = value.get("path", value.get("new_path", ""))
        old_path = value.get("old_path")
        status = value.get("status", "M")
        if (
            not isinstance(path, str)
            or not isinstance(status, str)
            or (old_path is not None and not isinstance(old_path, str))
        ):
            return Change(path="", status="UNKNOWN")
        return Change(path=path, status=status, old_path=old_path)
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        if len(value) in {2, 3} and isinstance(value[0], str) and isinstance(value[1], str):
            if len(value) == 3 and not isinstance(value[2], str):
                return Change(path="", status="UNKNOWN")
            old_path = value[2] if len(value) == 3 else None
            return Change(path=value[1], status=value[0], old_path=old_path)
    return Change(path="", status="UNKNOWN")


def _classify_path(path: str | None) -> tuple[str, str]:
    if path is None:
        return "L3", "unknown_path"

    lower = path.lower()
    if lower == ".github" or lower.startswith(".github/"):
        return "L3", "workflow"

    if lower == "scripts/build_release.ps1":
        return "L3", "package"
    if lower in _RELEASE_GOVERNANCE_PATHS:
        return "L3", "release_governance"

    name = PurePosixPath(lower).name
    if name in {"package.json", "package-lock.json", "pyproject.toml", "requirements.lock"}:
        return "L3", "package"
    if name == "public-source-policy.json":
        return "L3", "source_policy"
    if name == "security.md":
        return "L3", "security"

    if lower.startswith("docs/") or name in _DOC_NAMES or lower.endswith(_DOC_SUFFIXES):
        return "L0", "docs_only"
    for marker, reason in _HIGH_RISK_MARKERS:
        if marker in lower:
            return "L3", reason
    if lower.startswith("tests/") or lower.startswith("test/"):
        return "L1", "tests"
    if lower.startswith("src/") or lower.startswith("web/"):
        return "L2", "application_source"
    if lower.startswith("scripts/") or lower.startswith("tools/"):
        return "L2", "tooling"
    return "L3", "unknown_path"


def _classify_status(change: Change) -> tuple[str, str | None]:
    status = change.status.strip().upper()
    code = status.split(maxsplit=1)[0] if status else ""
    if code in {"D"} or code.startswith("R") or code.startswith("C"):
        reason = "rename_or_delete" if code.startswith(("D", "R")) else "copy_change"
        return "L3", reason
    if code == "U":
        return "L3", "unmerged_status"
    if code in {"M", "A", "T"}:
        if change.old_path is not None and change.old_path != change.path:
            return "L3", "rename_or_delete"
        return "L0", None
    return "L3", "unknown_status"


def _normalize_path(path: object) -> str | None:
    if not isinstance(path, str) or not path or "\x00" in path:
        return None
    value = path.replace("\\", "/")
    if value.startswith("/") or value.startswith("//") or _WINDOWS_ABSOLUTE.match(value):
        return None
    parts = []
    for part in value.split("/"):
        if part in {"", "."}:
            continue
        if part == "..":
            return None
        parts.append(part)
    return "/".join(parts) or None


def _normalize_level(level: object) -> str | None:
    if not isinstance(level, str):
        return None
    value = level.strip().upper()
    return value if value in _LEVEL_RANK else None


def _higher_level(left: str, right: str) -> str:
    return left if _LEVEL_RANK[left] >= _LEVEL_RANK[right] else right


def _add_reason(reasons: list[str], reason: str) -> None:
    if reason not in reasons:
        reasons.append(reason)


def _parse_change_argument(value: str) -> Change:
    status, separator, path = value.partition(":")
    if not separator:
        return Change(path=value)
    return Change(path=path, status=status)


def _read_stdin_changes() -> tuple[Change, ...]:
    if sys.stdin.isatty():
        return ()
    text = sys.stdin.read()
    if not text.strip():
        return ()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return parse_name_status(text.splitlines())
    if isinstance(payload, Mapping):
        if "changes" not in payload:
            return (Change(path="", status="UNKNOWN"),)
        payload = payload["changes"]
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        return tuple(_coerce_change(item) for item in payload)
    return (Change(path="", status="UNKNOWN"),)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Changed paths with the default M status")
    parser.add_argument(
        "--change",
        action="append",
        default=[],
        metavar="STATUS:PATH",
        help="A status/path pair, for example D:src/old.py or R100:src/new.py",
    )
    parser.add_argument("--declared-level", help="Optional risk floor; it cannot downgrade derived risk")
    parser.add_argument("--max-level", choices=LEVELS, help="Fail when the result exceeds this level")
    parser.add_argument("--json", action="store_true", help="Emit a JSON assessment")
    args = parser.parse_args(argv)

    changes = [Change(path=path) for path in args.paths]
    changes.extend(_parse_change_argument(value) for value in args.change)
    if not changes:
        changes.extend(_read_stdin_changes())
    assessment = classify_changes(changes, declared_level=args.declared_level)
    if args.json:
        print(json.dumps(assessment.as_dict(), sort_keys=True))
    else:
        print(assessment.level)
    if args.max_level is not None and _LEVEL_RANK[assessment.level] > _LEVEL_RANK[args.max_level]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
