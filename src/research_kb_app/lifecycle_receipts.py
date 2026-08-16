from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_kb_app.errors import AppOperationError


LIFECYCLE_RECEIPT_CONTRACT = "research-kb-app-lifecycle-receipt@1.0"
_STREAM = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_RECEIPT_NAME = re.compile(r"^appreceipt_([0-9]{20})_[0-9a-f-]{36}\.json$")


class AppendOnlyReceiptStore:
    def __init__(
        self,
        root: Path,
        stream: str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not _STREAM.fullmatch(stream):
            raise ValueError("receipt stream is invalid")
        self.root = Path(root)
        self.stream = stream
        self.clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()

    def append(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(payload, dict) or not payload:
            raise ValueError("receipt payload is invalid")
        with self._lock:
            self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
            sequence = self._next_sequence()
            receipt_id = f"appreceipt_{sequence:020d}_{uuid.uuid4()}"
            basis = {
                "contract_version": LIFECYCLE_RECEIPT_CONTRACT,
                "receipt_id": receipt_id,
                "sequence": sequence,
                "stream": self.stream,
                "payload": payload,
                "created_at": _timestamp(self.clock()),
            }
            record = {**basis, "receipt_digest": _digest(basis)}
            path = self.root / f"{receipt_id}.json"
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(_json_bytes(record))
                handle.flush()
                os.fsync(handle.fileno())
            return record

    def read_all(self) -> tuple[dict[str, Any], ...]:
        if not self.root.is_dir():
            return ()
        records = []
        for path in sorted(self.root.glob("appreceipt_*.json")):
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as error:
                raise AppOperationError("RKBAPP-RECEIPT", "App lifecycle receipt is unreadable") from error
            if not isinstance(value, dict) or value.get("contract_version") != LIFECYCLE_RECEIPT_CONTRACT:
                raise AppOperationError("RKBAPP-RECEIPT", "App lifecycle receipt contract is invalid")
            digest = value.get("receipt_digest")
            basis = {key: item for key, item in value.items() if key != "receipt_digest"}
            if value.get("stream") != self.stream or not isinstance(digest, str) or _digest(basis) != digest:
                raise AppOperationError("RKBAPP-RECEIPT", "App lifecycle receipt digest is invalid")
            records.append(value)
        if [record.get("sequence") for record in records] != list(range(1, len(records) + 1)):
            raise AppOperationError("RKBAPP-RECEIPT", "App lifecycle receipt sequence is invalid")
        return tuple(records)

    def _next_sequence(self) -> int:
        paths = sorted(self.root.glob("appreceipt_*.json"))
        if not paths:
            return 1
        match = _RECEIPT_NAME.fullmatch(paths[-1].name)
        if match is None:
            raise AppOperationError("RKBAPP-RECEIPT", "App lifecycle receipt filename is invalid")
        return int(match.group(1)) + 1


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value).rstrip(b"\n")).hexdigest()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("receipt clock must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = ["AppendOnlyReceiptStore", "LIFECYCLE_RECEIPT_CONTRACT"]
