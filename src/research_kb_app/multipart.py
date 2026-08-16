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
from typing import Any, BinaryIO

from python_multipart import MultipartParser
from python_multipart.exceptions import MultipartParseError
from python_multipart.multipart import parse_options_header

from research_kb_app.errors import AppOperationError


MANAGED_DIRECTORY = "upload-spool"
MARKER_NAME = ".research-kb-upload.json"
SPOOL_NAME = "source.pdf.partial"
MARKER_CONTRACT = "research-kb-app-upload-spool@1.0"
METADATA_LIMIT = 16 * 1024
ENVELOPE_LIMIT = 256 * 1024
PARSER_FEED_LIMIT = 64 * 1024


@dataclass(frozen=True, slots=True)
class ManagedUpload:
    operation_id: str
    operation_root: Path
    path: Path
    size_bytes: int
    sha256: str
    file_identity: tuple[int, int]

    def open(self) -> BinaryIO:
        current = _regular_file_identity(self.path)
        if current != self.file_identity:
            raise AppOperationError(
                "RKBAPP-UPLOAD-IDENTITY",
                "Managed upload changed before Core handoff",
                status_code=409,
            )
        return self.path.open("rb")

    def cleanup(self) -> bool:
        return _remove_owned_operation(
            self.operation_root,
            self.operation_id,
            expected_file_identity=self.file_identity,
        )


@dataclass(frozen=True, slots=True)
class ParsedUpload:
    metadata: dict[str, Any]
    upload: ManagedUpload


class _Collector:
    def __init__(self, handle: BinaryIO, max_pdf_bytes: int):
        self.handle = handle
        self.max_pdf_bytes = max_pdf_bytes
        self.metadata = bytearray()
        self.pending_file_blocks: list[bytes] = []
        self.file_size = 0
        self.file_digest = hashlib.sha256()
        self.file_prefix = bytearray()
        self.headers: dict[bytes, bytes] = {}
        self.header_field = bytearray()
        self.header_value = bytearray()
        self.current_part: str | None = None
        self.seen: set[str] = set()
        self.ended = False

    def on_part_begin(self) -> None:
        self.headers = {}
        self.header_field.clear()
        self.header_value.clear()
        self.current_part = None

    def on_header_field(self, data: bytes, start: int, end: int) -> None:
        self.header_field.extend(data[start:end])

    def on_header_value(self, data: bytes, start: int, end: int) -> None:
        self.header_value.extend(data[start:end])

    def on_header_end(self) -> None:
        try:
            name = bytes(self.header_field).strip().lower()
            value = bytes(self.header_value).strip()
        finally:
            self.header_field.clear()
            self.header_value.clear()
        if not name or name in self.headers:
            raise _bad_multipart("Multipart headers are invalid")
        if name not in {b"content-disposition", b"content-type"}:
            raise _bad_multipart("Multipart part contains an unsupported header")
        self.headers[name] = value

    def on_headers_finished(self) -> None:
        if set(self.headers) != {b"content-disposition", b"content-type"}:
            raise _bad_multipart("Multipart part headers do not match the contract")
        disposition, options = parse_options_header(self.headers[b"content-disposition"])
        name = options.get(b"name")
        if disposition != b"form-data" or name not in {b"file", b"metadata"}:
            raise _bad_multipart("Multipart part name is not accepted")
        part = name.decode("ascii")
        if part in self.seen:
            raise _bad_multipart("Multipart part is duplicated")
        content_type, _ = parse_options_header(self.headers[b"content-type"])
        if part == "file":
            if b"filename" not in options or content_type != b"application/pdf":
                raise _bad_multipart("Upload file part must be one PDF")
        elif b"filename" in options or content_type != b"application/json":
            raise _bad_multipart("Upload metadata part must be JSON")
        self.current_part = part

    def on_part_data(self, data: bytes, start: int, end: int) -> None:
        if self.current_part is None:
            raise _bad_multipart("Multipart data arrived before valid headers")
        block = bytes(data[start:end])
        if self.current_part == "file":
            if self.file_size + len(block) > self.max_pdf_bytes:
                raise AppOperationError(
                    "RKBAPP-UPLOAD-LIMIT",
                    "Upload PDF exceeds the Core byte budget",
                    status_code=413,
                )
            self.file_size += len(block)
            self.file_digest.update(block)
            if len(self.file_prefix) < 5:
                self.file_prefix.extend(block[: 5 - len(self.file_prefix)])
            if block:
                self.pending_file_blocks.append(block)
        else:
            if len(self.metadata) + len(block) > METADATA_LIMIT:
                raise AppOperationError(
                    "RKBAPP-UPLOAD-METADATA-LIMIT",
                    "Upload metadata exceeds its byte budget",
                    status_code=413,
                )
            self.metadata.extend(block)

    def on_part_end(self) -> None:
        if self.current_part is None:
            raise _bad_multipart("Multipart part did not contain valid headers")
        self.seen.add(self.current_part)
        self.current_part = None

    def on_end(self) -> None:
        self.ended = True

    async def flush(self) -> None:
        if not self.pending_file_blocks:
            return
        blocks = self.pending_file_blocks
        self.pending_file_blocks = []
        await asyncio.to_thread(_write_blocks, self.handle, blocks)


async def parse_multipart_stream(
    chunks: AsyncIterable[bytes],
    *,
    content_type: str,
    state_root: Path,
    max_pdf_bytes: int,
) -> ParsedUpload:
    media_type, options = parse_options_header(content_type)
    boundary = options.get(b"boundary")
    if media_type != b"multipart/form-data" or not boundary:
        raise AppOperationError(
            "RKBAPP-CONTENT-TYPE",
            "Upload requests require multipart form data",
            status_code=415,
        )
    if not isinstance(max_pdf_bytes, int) or max_pdf_bytes < 1:
        raise AppOperationError("RKBAPP-UPLOAD-LIMIT", "Core upload limit is unavailable")

    operation_id, operation_root, spool_path = _create_operation_root(Path(state_root))
    handle: BinaryIO | None = None
    total = 0
    try:
        handle = spool_path.open("xb")
        collector = _Collector(handle, max_pdf_bytes)
        callbacks = {
            "on_part_begin": collector.on_part_begin,
            "on_part_data": collector.on_part_data,
            "on_part_end": collector.on_part_end,
            "on_header_field": collector.on_header_field,
            "on_header_value": collector.on_header_value,
            "on_header_end": collector.on_header_end,
            "on_headers_finished": collector.on_headers_finished,
            "on_end": collector.on_end,
        }
        parser = MultipartParser(
            boundary,
            callbacks,
            max_size=max_pdf_bytes + METADATA_LIMIT + ENVELOPE_LIMIT,
            max_header_count=8,
            max_header_size=8192,
        )
        async for block in chunks:
            if not isinstance(block, bytes):
                raise _bad_multipart("Upload stream returned non-byte content")
            for offset in range(0, len(block), PARSER_FEED_LIMIT):
                piece = block[offset : offset + PARSER_FEED_LIMIT]
                total += len(piece)
                if total > max_pdf_bytes + METADATA_LIMIT + ENVELOPE_LIMIT:
                    raise AppOperationError(
                        "RKBAPP-UPLOAD-LIMIT",
                        "Multipart upload exceeds its envelope budget",
                        status_code=413,
                    )
                parser.write(piece)
                await collector.flush()
        parser.finalize()
        await collector.flush()
        if collector.seen != {"file", "metadata"} or not collector.ended:
            raise _bad_multipart("Multipart upload is incomplete")
        if collector.file_size < 1 or bytes(collector.file_prefix) != b"%PDF-":
            raise _bad_multipart("Upload content is not a PDF")
        await asyncio.to_thread(_flush_file, handle)
        handle.close()
        handle = None
        metadata = _decode_metadata(bytes(collector.metadata))
        identity = _regular_file_identity(spool_path)
        if identity is None:
            raise _bad_multipart("Managed upload spool is unavailable")
        return ParsedUpload(
            metadata=metadata,
            upload=ManagedUpload(
                operation_id=operation_id,
                operation_root=operation_root,
                path=spool_path,
                size_bytes=collector.file_size,
                sha256=collector.file_digest.hexdigest(),
                file_identity=identity,
            ),
        )
    except BaseException as error:
        if handle is not None:
            handle.close()
        _remove_owned_operation(operation_root, operation_id)
        if isinstance(error, (AppOperationError, asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
            raise
        if isinstance(error, (MultipartParseError, UnicodeError, json.JSONDecodeError, OSError, ValueError)):
            raise _bad_multipart("Multipart upload could not be parsed") from error
        raise


def cleanup_abandoned_uploads(state_root: Path) -> int:
    managed = Path(state_root) / MANAGED_DIRECTORY
    if not managed.exists() or _is_link(managed) or not managed.is_dir():
        return 0
    removed = 0
    for candidate in managed.iterdir():
        if _remove_owned_operation(candidate, candidate.name):
            removed += 1
    return removed


def _create_operation_root(state_root: Path) -> tuple[str, Path, Path]:
    managed = state_root / MANAGED_DIRECTORY
    managed.mkdir(mode=0o700, parents=True, exist_ok=True)
    if _is_link(managed) or not managed.is_dir():
        raise AppOperationError("RKBAPP-UPLOAD-SPOOL", "Managed upload root is unsafe")
    operation_id = uuid.uuid4().hex
    operation_root = managed / operation_id
    operation_root.mkdir(mode=0o700)
    marker = operation_root / MARKER_NAME
    marker.write_text(
        json.dumps(
            {
                "contract": MARKER_CONTRACT,
                "operation_id": operation_id,
                "spool_name": SPOOL_NAME,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return operation_id, operation_root, operation_root / SPOOL_NAME


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
        if payload != {
            "contract": MARKER_CONTRACT,
            "operation_id": operation_id,
            "spool_name": SPOOL_NAME,
        }:
            return False
        entries = list(operation_root.iterdir())
        if any(item.name not in {MARKER_NAME, SPOOL_NAME} or _is_link(item) for item in entries):
            return False
        spool = operation_root / SPOOL_NAME
        if spool.exists():
            identity = _regular_file_identity(spool)
            if identity is None or (
                expected_file_identity is not None and identity != expected_file_identity
            ):
                return False
            spool.unlink()
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


def _write_blocks(handle: BinaryIO, blocks: list[bytes]) -> None:
    for block in blocks:
        handle.write(block)


def _flush_file(handle: BinaryIO) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _decode_metadata(payload: bytes) -> dict[str, Any]:
    value = json.loads(payload.decode("utf-8"), object_pairs_hook=_unique_json_object)
    if not isinstance(value, dict):
        raise _bad_multipart("Upload metadata must be a JSON object")
    return value


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON object key")
        value[key] = item
    return value


def _bad_multipart(message: str) -> AppOperationError:
    return AppOperationError("RKBAPP-MULTIPART", message, status_code=400)


__all__ = [
    "ManagedUpload",
    "ParsedUpload",
    "cleanup_abandoned_uploads",
    "parse_multipart_stream",
]
