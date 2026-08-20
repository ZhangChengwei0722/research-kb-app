from __future__ import annotations

import argparse
import base64
import fnmatch
import hashlib
import json
import shutil
import stat
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Sequence


CONTRACT_VERSION = "research-kb-app-public-source@1"
TREE_DIGEST_ALGORITHM = "sha256_path_nul_content_v1"


class PublicSourceError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SourceEntry:
    path: str
    size: int
    sha256: str


def _run_git(repo_root: Path, *args: str) -> bytes:
    completed = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=False,
        capture_output=True,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", errors="replace").strip()
        raise PublicSourceError(f"git command failed: {stderr or completed.returncode}")
    return completed.stdout


def _canonical_relative_path(value: str) -> str:
    if not value or "\\" in value or "\0" in value or ":" in value:
        raise PublicSourceError(f"invalid relative path: {value!r}")
    candidate = PurePosixPath(value)
    if candidate.is_absolute() or any(part in {"", ".", ".."} for part in candidate.parts):
        raise PublicSourceError(f"unsafe relative path: {value!r}")
    normalized = candidate.as_posix()
    if normalized != value:
        raise PublicSourceError(f"non-canonical relative path: {value!r}")
    reserved = {"con", "prn", "aux", "nul", *(f"com{index}" for index in range(1, 10)), *(f"lpt{index}" for index in range(1, 10))}
    for part in candidate.parts:
        if part.endswith((" ", ".")) or part.split(".", 1)[0].casefold() in reserved:
            raise PublicSourceError(f"Windows-unsafe relative path: {value!r}")
    return normalized


def _is_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        attributes = path.lstat().st_file_attributes
    except (AttributeError, OSError):
        return False
    return bool(attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT)


def _assert_plain_file(repo_root: Path, relative_path: str) -> Path:
    path = repo_root.joinpath(*PurePosixPath(relative_path).parts)
    if not path.is_file() or _is_reparse(path):
        raise PublicSourceError(f"source entry is not a regular file: {relative_path}")
    current = path.parent
    while current != repo_root:
        if _is_reparse(current):
            raise PublicSourceError(f"source entry has a reparse ancestor: {relative_path}")
        current = current.parent
    return path


def _matches(path: str, patterns: Sequence[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in patterns)


def _validated_binary_patterns(policy: dict[str, object]) -> list[str]:
    patterns = policy.get("binary_allow", [])
    if not isinstance(patterns, list) or not all(isinstance(item, str) and item for item in patterns):
        raise PublicSourceError("policy field 'binary_allow' must be a string list when present")
    validated: list[str] = []
    for pattern in patterns:
        if "\\" in pattern or pattern.startswith("/") or pattern != pattern.strip():
            raise PublicSourceError(f"unsafe binary_allow pattern: {pattern!r}")
        parts = [part for part in pattern.split("/") if part not in {"", "."}]
        if any(part == ".." for part in parts):
            raise PublicSourceError(f"unsafe binary_allow pattern: {pattern!r}")
        if not pattern.lower().endswith(".png"):
            raise PublicSourceError(f"binary_allow pattern must target .png: {pattern!r}")
        validated.append(pattern)
    return validated


def load_policy(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PublicSourceError(f"cannot read public source policy: {error}") from error
    if not isinstance(payload, dict) or payload.get("contract_version") != CONTRACT_VERSION:
        raise PublicSourceError("unsupported public source policy contract")
    for key in ("allow", "deny", "forbidden_literal_b64", "secret_patterns"):
        values = payload.get(key)
        if not isinstance(values, list) or not all(isinstance(item, str) and item for item in values):
            raise PublicSourceError(f"policy field {key!r} must be a non-empty string list")
    _validated_binary_patterns(payload)
    max_file_bytes = payload.get("max_file_bytes")
    if not isinstance(max_file_bytes, int) or isinstance(max_file_bytes, bool) or max_file_bytes <= 0:
        raise PublicSourceError("policy max_file_bytes must be a positive integer")
    return payload


def _candidate_paths(repo_root: Path) -> list[str]:
    output = _run_git(
        repo_root,
        "ls-files",
        "-z",
        "--cached",
        "--others",
        "--exclude-standard",
    )
    paths = [item.decode("utf-8") for item in output.split(b"\0") if item]
    canonical = sorted({_canonical_relative_path(item) for item in paths})
    casefolded: dict[str, str] = {}
    for item in canonical:
        previous = casefolded.setdefault(item.casefold(), item)
        if previous != item:
            raise PublicSourceError(f"case-fold path collision: {previous!r} and {item!r}")
    return canonical


def _decoded_literals(policy: dict[str, object]) -> list[bytes]:
    decoded: list[bytes] = []
    for encoded in policy["forbidden_literal_b64"]:
        try:
            value = base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise PublicSourceError("policy contains invalid base64 forbidden literal") from error
        if not value:
            raise PublicSourceError("policy contains an empty forbidden literal")
        decoded.extend((value, value.replace(b"\\", b"\\\\")))
    return sorted(set(decoded))


_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _verified_png(payload: bytes) -> bool:
    if not payload.startswith(_PNG_SIGNATURE) or len(payload) < 57:
        return False
    if payload[12:16] != b"IHDR" or payload[-8:-4] != b"IEND" or payload[-4:] != b"\xaeB`\x82":
        return False
    width = int.from_bytes(payload[16:20], "big")
    height = int.from_bytes(payload[20:24], "big")
    return 1 <= width <= 8192 and 1 <= height <= 8192


def _scan_payload(relative_path: str, payload: bytes, policy: dict[str, object]) -> None:
    import re

    for literal in _decoded_literals(policy):
        if literal.lower() in payload.lower():
            raise PublicSourceError(f"forbidden private literal in {relative_path}")
    binary_allowed = _matches(relative_path, policy.get("binary_allow", []))
    if b"\0" in payload:
        if not (binary_allowed and _verified_png(payload)):
            raise PublicSourceError(f"binary payload is not allowed: {relative_path}")
        for pattern in policy["secret_patterns"]:
            compiled = re.compile(pattern.encode("ascii"), flags=re.IGNORECASE)
            if compiled.search(payload):
                raise PublicSourceError(f"secret-like value in {relative_path}")
        return
    try:
        text = payload.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise PublicSourceError(f"source file is not valid UTF-8: {relative_path}") from error
    for pattern in policy["secret_patterns"]:
        if re.search(pattern, text):
            raise PublicSourceError(f"secret-like value in {relative_path}")


def _tree_digest(entries: Iterable[tuple[str, bytes]]) -> str:
    digest = hashlib.sha256()
    for relative_path, payload in entries:
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(payload)
    return digest.hexdigest()


def build_manifest(repo_root: Path, policy_path: Path) -> dict[str, object]:
    repo_root = repo_root.resolve(strict=True)
    if _is_reparse(repo_root):
        raise PublicSourceError("repository root cannot be a symlink or reparse point")
    policy = load_policy(policy_path.resolve(strict=True))
    allowed: list[tuple[str, bytes]] = []
    denied: list[str] = []
    unclassified: list[str] = []
    max_file_bytes = int(policy["max_file_bytes"])
    for relative_path in _candidate_paths(repo_root):
        allow = _matches(relative_path, policy["allow"])
        deny = _matches(relative_path, policy["deny"])
        if allow and deny:
            raise PublicSourceError(f"path is both allowed and denied: {relative_path}")
        if deny:
            denied.append(relative_path)
            continue
        if not allow:
            unclassified.append(relative_path)
            continue
        source = _assert_plain_file(repo_root, relative_path)
        payload = source.read_bytes()
        if len(payload) > max_file_bytes:
            raise PublicSourceError(f"source file exceeds size limit: {relative_path}")
        _scan_payload(relative_path, payload, policy)
        allowed.append((relative_path, payload))
    if unclassified:
        raise PublicSourceError("unclassified source paths: " + ", ".join(unclassified))
    entries = [
        SourceEntry(path=path, size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        for path, payload in allowed
    ]
    head = _run_git(repo_root, "rev-parse", "HEAD").decode("ascii").strip()
    status = _run_git(repo_root, "status", "--porcelain=v1", "-z")
    manifest: dict[str, object] = {
        "contract_version": CONTRACT_VERSION,
        "source_head": head,
        "source_status_sha256": hashlib.sha256(status).hexdigest(),
        "policy_sha256": hashlib.sha256(policy_path.read_bytes()).hexdigest(),
        "tree_digest_algorithm": TREE_DIGEST_ALGORITHM,
        "tree_sha256": _tree_digest(allowed),
        "file_count": len(entries),
        "byte_count": sum(item.size for item in entries),
        "denied_path_count": len(denied),
        "files": [asdict(item) for item in entries],
    }
    canonical = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    manifest["manifest_payload_sha256"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return manifest


def write_manifest(manifest: dict[str, object], output: Path) -> None:
    if output.exists():
        raise PublicSourceError(f"manifest output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def materialize(repo_root: Path, policy_path: Path, output_root: Path) -> dict[str, object]:
    repo_root = repo_root.resolve(strict=True)
    output_root = output_root.resolve(strict=False)
    try:
        output_root.relative_to(repo_root)
    except ValueError:
        pass
    else:
        raise PublicSourceError("output root must be outside the source repository")
    if output_root.exists():
        raise PublicSourceError(f"output root already exists: {output_root}")
    manifest = build_manifest(repo_root, policy_path)
    output_root.mkdir(parents=True, exist_ok=False)
    try:
        for entry in manifest["files"]:
            relative_path = entry["path"]
            source = _assert_plain_file(repo_root, relative_path)
            destination = output_root.joinpath(*PurePosixPath(relative_path).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
        copied = _tree_from_directory(output_root)
        if copied != manifest["tree_sha256"]:
            raise PublicSourceError("materialized tree digest does not match manifest")
    except BaseException:
        shutil.rmtree(output_root, ignore_errors=True)
        raise
    return manifest


def materialize_with_manifest(
    repo_root: Path,
    policy_path: Path,
    output_root: Path,
    manifest_output: Path,
) -> dict[str, object]:
    output_root = output_root.resolve(strict=False)
    manifest_output = manifest_output.resolve(strict=False)
    if manifest_output.exists():
        raise PublicSourceError(f"manifest output already exists: {manifest_output}")
    try:
        manifest_output.relative_to(output_root)
    except ValueError:
        pass
    else:
        raise PublicSourceError("manifest output must be outside the materialized tree")

    manifest = materialize(repo_root, policy_path, output_root)
    try:
        write_manifest(manifest, manifest_output)
    except BaseException:
        shutil.rmtree(output_root, ignore_errors=True)
        raise
    return manifest


def _tree_from_directory(root: Path) -> str:
    entries: list[tuple[str, bytes]] = []
    for path in root.rglob("*"):
        if path.is_dir():
            if _is_reparse(path):
                raise PublicSourceError(f"materialized tree contains reparse directory: {path}")
            continue
        relative_path = path.relative_to(root).as_posix()
        if _is_reparse(path) or not path.is_file():
            raise PublicSourceError(f"materialized tree contains non-file: {relative_path}")
        entries.append((relative_path, path.read_bytes()))
    return _tree_digest(sorted(entries, key=lambda item: item[0]))


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify or materialize the public App source contract")
    subparsers = parser.add_subparsers(dest="command", required=True)
    manifest = subparsers.add_parser("manifest")
    manifest.add_argument("--repo-root", type=Path, required=True)
    manifest.add_argument("--policy", type=Path, required=True)
    manifest.add_argument("--output", type=Path, required=True)
    materialized = subparsers.add_parser("materialize")
    materialized.add_argument("--repo-root", type=Path, required=True)
    materialized.add_argument("--policy", type=Path, required=True)
    materialized.add_argument("--output", type=Path, required=True)
    materialized.add_argument("--manifest-output", type=Path)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        if args.command == "manifest":
            write_manifest(build_manifest(args.repo_root, args.policy), args.output)
        else:
            manifest_output = args.manifest_output or (
                args.output.parent / f"{args.output.name}-manifest.json"
            )
            materialize_with_manifest(args.repo_root, args.policy, args.output, manifest_output)
    except (OSError, UnicodeError, PublicSourceError) as error:
        print(f"public source contract failed: {error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
