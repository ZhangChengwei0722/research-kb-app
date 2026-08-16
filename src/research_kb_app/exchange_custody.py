from __future__ import annotations

import asyncio
import hashlib
import json
import os
import stat
import uuid
from collections.abc import AsyncIterable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from research_kb_app.errors import AppOperationError


MANAGED_DIRECTORY = "exchange-spool"
MARKER_NAME = ".research-kb-exchange.json"
ARCHIVE_NAME = "bundle.rkb-exchange.zip"
MARKER_CONTRACT = "research-kb-app-exchange-spool@1.0"
STREAM_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True, slots=True)
class ManagedExchangeFile:
    operation_id: str
    operation_root: Path
    path: Path
    size_bytes: int
    sha256: str
    file_identity: tuple[int, int]

    def validate(self) -> None:
        if _regular_file_identity(self.path) != self.file_identity:
            raise AppOperationError(
                "RKBAPP-EXCHANGE-IDENTITY",
                "Managed Exchange archive changed before use",
                status_code=409,
            )
        try:
            size = self.path.stat().st_size
        except OSError as error:
            raise AppOperationError(
                "RKBAPP-EXCHANGE-IDENTITY",
                "Managed Exchange archive is unavailable",
                status_code=409,
            ) from error
        digest, prefix = _hash_and_prefix(self.path)
        if size != self.size_bytes or digest != self.sha256 or prefix[:4] != b"PK\x03\x04":
            raise AppOperationError(
                "RKBAPP-EXCHANGE-CONTENT",
                "Managed Exchange archive content changed before use",
                status_code=409,
            )

    def open(self) -> BinaryIO:
        self.validate()
        return self.path.open("rb")

    def cleanup(self) -> bool:
        return _remove_owned_operation(
            self.operation_root,
            self.operation_id,
            expected_file_identity=self.file_identity,
        )


@dataclass(frozen=True, slots=True)
class ManagedExchangeOutput:
    operation_id: str
    operation_root: Path
    path: Path

    def finalize(self, *, max_archive_bytes: int) -> ManagedExchangeFile:
        identity = _regular_file_identity(self.path)
        if identity is None:
            raise AppOperationError("RKBAPP-EXCHANGE-OUTPUT", "Exchange output was not created")
        size = self.path.stat().st_size
        if not 1 <= size <= max_archive_bytes:
            raise AppOperationError("RKBAPP-EXCHANGE-LIMIT", "Exchange output exceeds its byte budget")
        digest, prefix = _hash_and_prefix(self.path)
        if prefix[:4] != b"PK\x03\x04":
            raise AppOperationError("RKBAPP-EXCHANGE-ARCHIVE", "Exchange output is not a canonical archive")
        return ManagedExchangeFile(
            operation_id=self.operation_id,
            operation_root=self.operation_root,
            path=self.path,
            size_bytes=size,
            sha256=digest,
            file_identity=identity,
        )

    def cleanup(self) -> bool:
        return _remove_owned_operation(self.operation_root, self.operation_id)


async def stream_exchange_upload(
    chunks: AsyncIterable[bytes],
    *,
    state_root: Path,
    max_archive_bytes: int,
    declared_length: int,
) -> ManagedExchangeFile:
    if not isinstance(declared_length, int) or declared_length < 1:
        raise AppOperationError(
            "RKBAPP-CONTENT-LENGTH",
            "Exchange upload requires a positive Content-Length",
            status_code=411,
        )
    if not isinstance(max_archive_bytes, int) or max_archive_bytes < 1:
        raise AppOperationError("RKBAPP-EXCHANGE-LIMIT", "Exchange upload budget is unavailable")
    if declared_length > max_archive_bytes:
        raise AppOperationError(
            "RKBAPP-EXCHANGE-LIMIT",
            "Exchange upload exceeds the safe-reader archive budget",
            status_code=413,
        )

    output = _create_operation(Path(state_root), "upload")
    handle: BinaryIO | None = None
    digest = hashlib.sha256()
    prefix = bytearray()
    total = 0
    try:
        handle = output.path.open("xb")
        async for block in chunks:
            if not isinstance(block, bytes):
                raise AppOperationError("RKBAPP-EXCHANGE-UPLOAD", "Exchange upload returned non-byte content")
            for offset in range(0, len(block), STREAM_CHUNK_BYTES):
                piece = block[offset : offset + STREAM_CHUNK_BYTES]
                total += len(piece)
                if total > declared_length or total > max_archive_bytes:
                    raise AppOperationError(
                        "RKBAPP-EXCHANGE-LIMIT",
                        "Exchange upload exceeds its declared or safe-reader budget",
                        status_code=413,
                    )
                digest.update(piece)
                if len(prefix) < 4:
                    prefix.extend(piece[: 4 - len(prefix)])
                if piece:
                    await asyncio.to_thread(handle.write, piece)
        if total != declared_length:
            raise AppOperationError(
                "RKBAPP-CONTENT-LENGTH",
                "Exchange upload length does not match Content-Length",
                status_code=400,
            )
        if bytes(prefix) != b"PK\x03\x04":
            raise AppOperationError(
                "RKBAPP-EXCHANGE-ARCHIVE",
                "Exchange upload is not a supported archive",
                status_code=400,
            )
        await asyncio.to_thread(_flush_file, handle)
        handle.close()
        handle = None
        identity = _regular_file_identity(output.path)
        if identity is None:
            raise AppOperationError("RKBAPP-EXCHANGE-UPLOAD", "Managed Exchange upload is unavailable")
        return ManagedExchangeFile(
            operation_id=output.operation_id,
            operation_root=output.operation_root,
            path=output.path,
            size_bytes=total,
            sha256=digest.hexdigest(),
            file_identity=identity,
        )
    except BaseException:
        if handle is not None:
            handle.close()
        output.cleanup()
        raise


def create_exchange_output(state_root: Path) -> ManagedExchangeOutput:
    return _create_operation(Path(state_root), "download")


def cleanup_abandoned_exchange_files(state_root: Path) -> int:
    managed = Path(state_root) / MANAGED_DIRECTORY
    if not managed.exists() or _is_link(managed) or not managed.is_dir():
        return 0
    removed = 0
    for candidate in managed.iterdir():
        if _remove_owned_operation(candidate, candidate.name):
            removed += 1
    return removed


def _create_operation(state_root: Path, kind: str) -> ManagedExchangeOutput:
    managed = state_root / MANAGED_DIRECTORY
    managed.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _is_link(managed) or not managed.is_dir():
        raise AppOperationError("RKBAPP-EXCHANGE-SPOOL", "Managed Exchange root is unsafe")
    operation_id = uuid.uuid4().hex
    operation_root = managed / operation_id
    operation_root.mkdir(mode=0o700)
    marker = operation_root / MARKER_NAME
    marker.write_text(
        json.dumps(
            {
                "contract": MARKER_CONTRACT,
                "operation_id": operation_id,
                "kind": kind,
                "archive_name": ARCHIVE_NAME,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return ManagedExchangeOutput(operation_id, operation_root, operation_root / ARCHIVE_NAME)


def _remove_owned_operation(
    operation_root: Path,
    operation_id: str,
    *,
    expected_file_identity: tuple[int, int] | None = None,
) -> bool:
    try:
        if _is_link(operation_root) or not operation_root.is_dir() or operation_root.name != operation_id:
            return False
        marker = operation_root / MARKER_NAME
        if _is_link(marker) or not marker.is_file():
            return False
        payload = json.loads(marker.read_text(encoding="utf-8"))
        if payload.get("contract") != MARKER_CONTRACT or payload.get("operation_id") != operation_id:
            return False
        if payload.get("kind") not in {"upload", "download"} or payload.get("archive_name") != ARCHIVE_NAME:
            return False
        entries = list(operation_root.iterdir())
        if any(item.name not in {MARKER_NAME, ARCHIVE_NAME} or _is_link(item) for item in entries):
            return False
        archive = operation_root / ARCHIVE_NAME
        if archive.exists():
            identity = _regular_file_identity(archive)
            if identity is None or (
                expected_file_identity is not None and identity != expected_file_identity
            ):
                return False
            archive.unlink()
        marker.unlink()
        operation_root.rmdir()
        return True
    except (OSError, ValueError, json.JSONDecodeError):
        return False


def _regular_file_identity(path: Path) -> tuple[int, int] | None:
    try:
        value = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISREG(value.st_mode):
        return None
    return value.st_dev, value.st_ino


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    is_junction = getattr(path, "is_junction", None)
    if callable(is_junction) and is_junction():
        return True
    try:
        attributes = getattr(os.lstat(path), "st_file_attributes", 0)
    except OSError:
        return False
    return bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0))


def _hash_and_prefix(path: Path) -> tuple[str, bytes]:
    digest = hashlib.sha256()
    prefix = b""
    with path.open("rb") as handle:
        while block := handle.read(STREAM_CHUNK_BYTES):
            digest.update(block)
            if len(prefix) < 4:
                prefix = (prefix + block)[:4]
    return digest.hexdigest(), prefix


def _flush_file(handle: BinaryIO) -> None:
    handle.flush()
    os.fsync(handle.fileno())


__all__ = [
    "ManagedExchangeFile",
    "ManagedExchangeOutput",
    "cleanup_abandoned_exchange_files",
    "create_exchange_output",
    "stream_exchange_upload",
]
