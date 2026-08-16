from __future__ import annotations

import argparse
import base64
import configparser
import hashlib
import json
import re
import stat
import tarfile
import tomllib
import zipfile
from dataclasses import asdict, dataclass
from email.parser import BytesParser
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name


EXPECTED_NAME = "research-kb-app"
EXPECTED_LICENSE = "Apache-2.0"


class ArtifactPolicyError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ArchiveReport:
    filename: str
    sha256: str
    file_count: int
    byte_count: int


def _safe_path(value: str) -> str:
    if not value or "\\" in value or "\0" in value or ":" in value:
        raise ArtifactPolicyError(f"unsafe archive path: {value!r}")
    trimmed = value.rstrip("/")
    if not trimmed or trimmed == ".":
        raise ArtifactPolicyError(f"unsafe archive path: {value!r}")
    path = PurePosixPath(trimmed)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ArtifactPolicyError(f"unsafe archive path: {value!r}")
    normalized = path.as_posix()
    if normalized != trimmed:
        raise ArtifactPolicyError(f"unsafe archive path: {value!r}")
    return normalized


def _check_unique(paths: Iterable[str]) -> None:
    exact: set[str] = set()
    folded: dict[str, str] = {}
    for path in paths:
        if path in exact:
            raise ArtifactPolicyError(f"duplicate archive path: {path}")
        exact.add(path)
        previous = folded.setdefault(path.casefold(), path)
        if previous != path:
            raise ArtifactPolicyError(f"case-fold archive collision: {previous}, {path}")


def _forbidden_literals() -> tuple[bytes, ...]:
    encoded = (
        "QzpcVXNlcnNcMzI1NjNcRG9jdW1lbnRzXENvZGV4",
        "RTpc6JuL55m96LSo6ZmN6Kej",
        "cHJpdmF0ZV93b3Jrc3BhY2Vz",
        "cmVzZWFyY2gta2ItY29yZS1wMmI=",
    )
    return tuple(base64.b64decode(item) for item in encoded)


def _scan_payload(path: str, payload: bytes) -> None:
    lowered = payload.lower()
    for literal in _forbidden_literals():
        if literal.lower() in lowered or literal.replace(b"\\", b"\\\\").lower() in lowered:
            raise ArtifactPolicyError(f"private literal in archive payload: {path}")
    secret_patterns = (
        rb"gh[pousr]_[A-Za-z0-9]{30,}",
        rb"sk-[A-Za-z0-9_-]{24,}",
        rb"AKIA[0-9A-Z]{16}",
        rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    )
    for pattern in secret_patterns:
        if re.search(pattern, payload, flags=re.IGNORECASE):
            raise ArtifactPolicyError(f"secret-like value in archive payload: {path}")


def _scan_archive_path(path: str) -> None:
    _scan_payload(path, path.encode("utf-8"))


def _validate_core_requirement(requirements: Sequence[str]) -> None:
    core_requirements: list[Requirement] = []
    for value in requirements:
        try:
            requirement = Requirement(value)
        except InvalidRequirement as error:
            raise ArtifactPolicyError("wheel contains invalid Requires-Dist metadata") from error
        if canonicalize_name(requirement.name) == "research-kb-core":
            core_requirements.append(requirement)
    if len(core_requirements) != 1:
        raise ArtifactPolicyError("wheel must contain exactly one Core Requires-Dist")
    requirement = core_requirements[0]
    if (
        requirement.extras != {"pdf"}
        or str(requirement.specifier) != "==0.1.1"
        or requirement.marker is not None
        or requirement.url is not None
    ):
        raise ArtifactPolicyError("wheel exact Core Requires-Dist is missing")


def _validate_public_source_policy(payload: bytes) -> None:
    try:
        policy = json.loads(payload.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ArtifactPolicyError("sdist public source policy is invalid") from error
    if not isinstance(policy, dict) or policy.get("contract_version") != "research-kb-app-public-source@1":
        raise ArtifactPolicyError("sdist public source policy contract mismatch")
    for key in ("allow", "deny", "forbidden_literal_b64", "secret_patterns"):
        values = policy.get(key)
        if not isinstance(values, list) or not values or not all(isinstance(item, str) and item for item in values):
            raise ArtifactPolicyError(f"sdist public source policy field {key!r} is invalid")
    max_file_bytes = policy.get("max_file_bytes")
    if not isinstance(max_file_bytes, int) or isinstance(max_file_bytes, bool) or max_file_bytes <= 0:
        raise ArtifactPolicyError("sdist public source policy size limit is invalid")


def _artifact_report(path: Path, payloads: dict[str, bytes]) -> ArchiveReport:
    return ArchiveReport(
        filename=path.name,
        sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        file_count=len(payloads),
        byte_count=sum(len(payload) for payload in payloads.values()),
    )


def _wheel_payloads(path: Path) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(path) as archive:
            paths: list[str] = []
            for item in archive.infolist():
                normalized = _safe_path(item.filename)
                _scan_archive_path(normalized)
                paths.append(normalized)
                mode = item.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if item.is_dir():
                    if file_type and file_type != stat.S_IFDIR:
                        raise ArtifactPolicyError(f"non-directory wheel entry: {normalized}")
                    continue
                if file_type and file_type != stat.S_IFREG:
                    raise ArtifactPolicyError(f"non-regular wheel entry: {normalized}")
                payload = archive.read(item)
                _scan_payload(normalized, payload)
                payloads[normalized] = payload
            _check_unique(paths)
    except (OSError, zipfile.BadZipFile) as error:
        raise ArtifactPolicyError(f"cannot read wheel: {path.name}") from error
    return payloads


def _validate_wheel_entry_points(payload: bytes) -> None:
    try:
        text = payload.decode("utf-8")
    except UnicodeError as error:
        raise ArtifactPolicyError("wheel entry_points.txt is not UTF-8") from error
    parser = configparser.ConfigParser(
        interpolation=None,
        delimiters=("=",),
        strict=True,
    )
    # Preserve option-name case. ConfigParser lowercases by default, which would
    # let "Research-KB-App" masquerade as the approved console script name.
    parser.optionxform = str
    try:
        parser.read_string(text)
    except configparser.Error as error:
        raise ArtifactPolicyError("wheel entry_points.txt is invalid") from error
    if parser.defaults():
        raise ArtifactPolicyError("wheel entry_points.txt must not define defaults")
    if set(parser.sections()) != {"console_scripts"}:
        raise ArtifactPolicyError("wheel entry_points.txt must contain only console_scripts")
    scripts = parser["console_scripts"]
    if set(scripts) != {"research-kb-app"}:
        raise ArtifactPolicyError("wheel must contain exactly one approved console script")
    if scripts["research-kb-app"] != "research_kb_app.entrypoint:main":
        raise ArtifactPolicyError("wheel console script target mismatch")


def validate_wheel(path: Path, expected_version: str) -> ArchiveReport:
    payloads = _wheel_payloads(path)
    dist_info = f"research_kb_app-{expected_version}.dist-info"
    metadata_path = f"{dist_info}/METADATA"
    entry_points_path = f"{dist_info}/entry_points.txt"
    required = {
        metadata_path,
        f"{dist_info}/licenses/LICENSE",
        entry_points_path,
        "research_kb_app/__init__.py",
        "research_kb_app/core-compatibility.json",
        "research_kb_app/web_dist/index.html",
    }
    missing = sorted(required - payloads.keys())
    if missing:
        raise ArtifactPolicyError("wheel required files missing: " + ", ".join(missing))
    _validate_wheel_entry_points(payloads[entry_points_path])
    if not any(path.startswith("research_kb_app/web_dist/assets/") for path in payloads):
        raise ArtifactPolicyError("wheel frontend assets are missing")
    forbidden = [
        name
        for name in payloads
        if name.startswith(("tests/", ".github/", "docs/")) or "/docs/receipts/" in name
    ]
    if forbidden:
        raise ArtifactPolicyError("wheel contains forbidden source files: " + ", ".join(forbidden))
    metadata = BytesParser().parsebytes(payloads[metadata_path])
    if metadata.get("Name") != EXPECTED_NAME or metadata.get("Version") != expected_version:
        raise ArtifactPolicyError("wheel package name or version mismatch")
    if metadata.get("License-Expression") != EXPECTED_LICENSE:
        raise ArtifactPolicyError("wheel License-Expression mismatch")
    requirements = metadata.get_all("Requires-Dist", failobj=[])
    _validate_core_requirement(requirements)
    return _artifact_report(path, payloads)


def _sdist_payloads(path: Path, expected_root: str) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    try:
        with tarfile.open(path, mode="r:gz") as archive:
            members = archive.getmembers()
            raw_paths = [_safe_path(item.name) for item in members]
            for raw_path in raw_paths:
                _scan_archive_path(raw_path)
            _check_unique(raw_paths)
            roots = {PurePosixPath(name).parts[0] for name in raw_paths}
            if len(roots) != 1:
                raise ArtifactPolicyError("sdist must have one top-level directory")
            root = next(iter(roots))
            if root != expected_root:
                raise ArtifactPolicyError("sdist top-level directory mismatch")
            for member, raw_path in zip(members, raw_paths, strict=True):
                if member.isdir():
                    continue
                if not member.isfile() or member.issym() or member.islnk():
                    raise ArtifactPolicyError(f"non-regular sdist entry: {raw_path}")
                relative = PurePosixPath(raw_path).relative_to(root).as_posix()
                stream = archive.extractfile(member)
                if stream is None:
                    raise ArtifactPolicyError(f"cannot read sdist entry: {relative}")
                payload = stream.read()
                _scan_payload(relative, payload)
                payloads[relative] = payload
    except (OSError, tarfile.TarError) as error:
        raise ArtifactPolicyError(f"cannot read sdist: {path.name}") from error
    return payloads


def validate_sdist(path: Path, expected_version: str) -> ArchiveReport:
    expected_root = f"research_kb_app-{expected_version}"
    expected_filename = f"{expected_root}.tar.gz"
    if path.name != expected_filename:
        raise ArtifactPolicyError("sdist filename/version mismatch")
    payloads = _sdist_payloads(path, expected_root)
    required = {
        "LICENSE",
        "README.md",
        "SECURITY.md",
        "SUPPORT.md",
        "CONTRIBUTING.md",
        "CHANGELOG.md",
        "pyproject.toml",
        "public-source-policy.json",
        "docs/architecture.md",
        "docs/configuration.md",
        "docs/workflow.md",
        "docs/r1-operator-guide.md",
        "src/research_kb_app/__init__.py",
        "tests/fixtures/p2_small/fixture-manifest.json",
        "web/release/index.html",
    }
    missing = sorted(required - payloads.keys())
    if missing:
        raise ArtifactPolicyError("sdist required files missing: " + ", ".join(missing))
    forbidden = [
        name
        for name in payloads
        if name == ".git"
        or name.startswith((".git/", "docs/receipts/"))
        or re.fullmatch(r"docs/p[^/]*\.md", name, flags=re.IGNORECASE)
    ]
    if forbidden:
        raise ArtifactPolicyError("sdist contains forbidden history: " + ", ".join(forbidden))
    if not any(name.startswith("web/release/assets/") for name in payloads):
        raise ArtifactPolicyError("sdist frontend assets are missing")
    _validate_public_source_policy(payloads["public-source-policy.json"])
    try:
        project = tomllib.loads(payloads["pyproject.toml"].decode("utf-8"))["project"]
    except (UnicodeError, tomllib.TOMLDecodeError, KeyError, TypeError) as error:
        raise ArtifactPolicyError("sdist pyproject metadata is invalid") from error
    if project.get("name") != EXPECTED_NAME or project.get("version") != expected_version:
        raise ArtifactPolicyError("sdist package name or version mismatch")
    if project.get("scripts") != {"research-kb-app": "research_kb_app.entrypoint:main"}:
        raise ArtifactPolicyError("sdist console script mapping mismatch")
    return _artifact_report(path, payloads)


def validate_dist(dist_dir: Path, expected_version: str) -> dict[str, object]:
    dist_dir = dist_dir.resolve(strict=True)
    wheel_pattern = f"research_kb_app-{expected_version}-py3-none-any.whl"
    sdist_pattern = f"research_kb_app-{expected_version}.tar.gz"
    entries = sorted(path.name for path in dist_dir.iterdir())
    if entries != sorted((wheel_pattern, sdist_pattern)):
        raise ArtifactPolicyError("dist directory must contain only one exact wheel and one exact sdist")
    wheels = sorted(dist_dir.glob(wheel_pattern))
    sdists = sorted(dist_dir.glob(sdist_pattern))
    if len(wheels) != 1 or len(sdists) != 1:
        raise ArtifactPolicyError("dist directory must contain one exact wheel and one exact sdist")
    wheel = validate_wheel(wheels[0], expected_version)
    sdist = validate_sdist(sdists[0], expected_version)
    return {
        "contract_version": "research-kb-app-release-artifacts@1",
        "expected_name": EXPECTED_NAME,
        "expected_version": expected_version,
        "wheel": asdict(wheel),
        "sdist": asdict(sdist),
        "valid": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate Research KB App wheel and sdist")
    parser.add_argument("--dist-dir", type=Path, required=True)
    parser.add_argument("--expected-version", required=True)
    args = parser.parse_args(argv)
    try:
        report = validate_dist(args.dist_dir, args.expected_version)
    except (OSError, ArtifactPolicyError) as error:
        print(json.dumps({"valid": False, "error": str(error)}, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
