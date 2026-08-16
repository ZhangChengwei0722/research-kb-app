from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


CONTRACT_VERSION = "public-p2-small-fixture@1"
DIGEST_ALGORITHM = "sha256_path_nul_content_v1"
FIXTURE_ID = "p2-small"
FIXTURE_ORIGIN = "synthetic_from_scratch"
GENERATOR_CONTRACT_VERSION = "p2-catalog-generator@1.0"
SOURCE_GENERATOR_MANIFEST_SHA256 = "5e22b93e83fcd3d04060edec8974ba3852340df10e91a1e4b765e2aa90fa566d"
SOURCE_CANONICAL_TREE_SHA256 = "97b6f5ec1ee9bb8a12c30c33e2c19da2d6b1834800b5d397f21666d3f97e59a5"
_MANIFEST_NAME = "fixture-manifest.json"
_SHA256_RE = re.compile(r"\A[0-9a-f]{64}\Z")
_REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class FixtureContractError(RuntimeError):
    """Raised when a public synthetic fixture does not match its pinned contract."""


@dataclass(frozen=True, slots=True)
class FixtureVerification:
    root: Path
    file_count: int
    total_bytes: int
    whole_tree_digest: str


@dataclass(frozen=True, slots=True)
class _FileRecord:
    size: int
    sha256: str
    content: bytes


def default_manifest_path() -> Path:
    return Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "p2_small" / _MANIFEST_NAME


def default_fixture_root() -> Path:
    return default_manifest_path().parent / "workspace"


def sha256_path_nul_content_v1(root: Path | str) -> str:
    """Hash a complete regular-file tree using the public fixture algorithm."""

    records = _collect_files(_absolute_path(root))
    return _digest_records(records)


def verify_fixture(
    fixture_root: Path | str,
    *,
    manifest_path: Path | str | None = None,
) -> FixtureVerification:
    """Fail closed unless ``fixture_root`` exactly matches the pinned manifest."""

    root = _absolute_path(fixture_root)
    _require_directory(root, "fixture root")
    manifest = _absolute_path(manifest_path or default_manifest_path())
    contract = _load_manifest(manifest)
    actual = _collect_files(root)
    expected = contract["files"]

    actual_paths = set(actual)
    expected_paths = set(expected)
    missing = sorted(expected_paths - actual_paths)
    extra = sorted(actual_paths - expected_paths)
    if missing or extra:
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if extra:
            details.append("extra=" + ",".join(extra))
        raise FixtureContractError("fixture file set mismatch: " + "; ".join(details))

    for relative_path in sorted(expected):
        expected_file = expected[relative_path]
        actual_file = actual[relative_path]
        if actual_file.size != expected_file["size"]:
            raise FixtureContractError(
                f"fixture size mismatch for {relative_path}: "
                f"expected {expected_file['size']}, got {actual_file.size}"
            )
        if actual_file.sha256 != expected_file["sha256"]:
            raise FixtureContractError(
                f"fixture digest mismatch for {relative_path}: "
                f"expected {expected_file['sha256']}, got {actual_file.sha256}"
            )

    whole_tree_digest = _digest_records(actual)
    if whole_tree_digest != contract["whole_tree_digest"]:
        raise FixtureContractError(
            "fixture whole-tree digest mismatch: "
            f"expected {contract['whole_tree_digest']}, got {whole_tree_digest}"
        )

    return FixtureVerification(
        root=root,
        file_count=len(actual),
        total_bytes=sum(record.size for record in actual.values()),
        whole_tree_digest=whole_tree_digest,
    )


def _absolute_path(value: Path | str) -> Path:
    return Path(os.path.abspath(os.fspath(value)))


def _load_manifest(path: Path) -> dict[str, Any]:
    _require_regular_file(path, "fixture contract manifest")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FixtureContractError(f"fixture contract manifest is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise FixtureContractError("fixture contract manifest must be a JSON object")

    expected_fields = {
        "contract_version": CONTRACT_VERSION,
        "fixture_id": FIXTURE_ID,
        "fixture_origin": FIXTURE_ORIGIN,
        "generator_contract_version": GENERATOR_CONTRACT_VERSION,
        "source_generator_manifest_sha256": SOURCE_GENERATOR_MANIFEST_SHA256,
        "source_canonical_tree_sha256": SOURCE_CANONICAL_TREE_SHA256,
        "digest_algorithm": DIGEST_ALGORITHM,
        "payload_root": "workspace",
    }
    for field, expected_value in expected_fields.items():
        if payload.get(field) != expected_value:
            raise FixtureContractError(
                f"fixture contract field {field!r} must equal {expected_value!r}"
            )

    file_specs = payload.get("files")
    if not isinstance(file_specs, list):
        raise FixtureContractError("fixture contract files must be a list")
    files: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(file_specs):
        if not isinstance(item, dict):
            raise FixtureContractError(f"fixture contract file entry {index} is not an object")
        relative_path = item.get("path")
        _validate_relative_path(relative_path, f"manifest file entry {index}")
        if relative_path in files:
            raise FixtureContractError(f"duplicate fixture contract path: {relative_path}")
        size = item.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise FixtureContractError(f"invalid fixture size for {relative_path}")
        sha256 = item.get("sha256")
        if not isinstance(sha256, str) or not _SHA256_RE.fullmatch(sha256):
            raise FixtureContractError(f"invalid fixture SHA-256 for {relative_path}")
        files[relative_path] = {"size": size, "sha256": sha256}

    file_count = payload.get("file_count")
    if isinstance(file_count, bool) or not isinstance(file_count, int) or file_count != len(files):
        raise FixtureContractError("fixture contract file_count does not match files")
    total_bytes = payload.get("total_bytes")
    if (
        isinstance(total_bytes, bool)
        or not isinstance(total_bytes, int)
        or total_bytes != sum(item["size"] for item in files.values())
    ):
        raise FixtureContractError("fixture contract total_bytes does not match files")
    whole_tree_digest = payload.get("whole_tree_digest")
    if not isinstance(whole_tree_digest, str) or not _SHA256_RE.fullmatch(whole_tree_digest):
        raise FixtureContractError("invalid fixture whole_tree_digest")
    return {"files": files, "whole_tree_digest": whole_tree_digest}


def _validate_relative_path(value: object, context: str) -> None:
    if not isinstance(value, str) or not value or "\\" in value:
        raise FixtureContractError(f"invalid traversal path in {context}: {value!r}")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or any(part in {"", ".", ".."} for part in path.parts)
        or (len(value) >= 2 and value[1] == ":")
    ):
        raise FixtureContractError(f"invalid traversal path in {context}: {value!r}")


def _collect_files(root: Path) -> dict[str, _FileRecord]:
    _require_directory(root, "fixture root")
    records: dict[str, _FileRecord] = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(os.scandir(directory), key=lambda entry: entry.name)
        except OSError as exc:
            raise FixtureContractError(f"fixture directory is unreadable: {directory}") from exc
        for entry in entries:
            child = Path(entry.path)
            try:
                metadata = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise FixtureContractError(f"fixture entry is unreadable: {child}") from exc
            _reject_reparse(metadata, child)
            relative_path = child.relative_to(root).as_posix()
            _validate_relative_path(relative_path, "fixture entry")
            if stat.S_ISDIR(metadata.st_mode):
                pending.append(child)
                continue
            if not stat.S_ISREG(metadata.st_mode):
                raise FixtureContractError(f"fixture entry is not a regular file: {relative_path}")
            try:
                content = child.read_bytes()
            except OSError as exc:
                raise FixtureContractError(f"fixture file is unreadable: {relative_path}") from exc
            _reject_reparse(child.lstat(), child)
            if len(content) != metadata.st_size:
                raise FixtureContractError(f"fixture file changed while reading: {relative_path}")
            records[relative_path] = _FileRecord(
                size=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                content=content,
            )
    return records


def _digest_records(records: dict[str, _FileRecord]) -> str:
    digest = hashlib.sha256()
    for relative_path in sorted(records):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(records[relative_path].content)
    return digest.hexdigest()


def _require_directory(path: Path, description: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise FixtureContractError(f"{description} is missing: {path}") from exc
    _reject_reparse(metadata, path)
    if not stat.S_ISDIR(metadata.st_mode):
        raise FixtureContractError(f"{description} is not a directory: {path}")


def _require_regular_file(path: Path, description: str) -> None:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise FixtureContractError(f"{description} is missing: {path}") from exc
    _reject_reparse(metadata, path)
    if not stat.S_ISREG(metadata.st_mode):
        raise FixtureContractError(f"{description} is not a regular file: {path}")


def _reject_reparse(metadata: os.stat_result, path: Path) -> None:
    if stat.S_ISLNK(metadata.st_mode) or bool(getattr(metadata, "st_file_attributes", 0) & _REPARSE_POINT):
        raise FixtureContractError(f"fixture symlink/reparse point is forbidden: {path}")
