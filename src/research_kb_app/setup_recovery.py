from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from research_kb.workspace_materialization import (
    ExternalSourceRoot,
    RootSecurityAttestation,
    WorkspaceMaterializationProposal,
    WorkspaceMaterializationReceipt,
    WorkspaceMaterializationRequest,
)

from research_kb_app.errors import AppOperationError


SETUP_RECOVERY_CONTRACT = "research-kb-app-setup-recovery@1.0"
_STATES = {
    "prepared",
    "commit_started",
    "core_committed",
    "profile_committed",
    "recovery_discarded",
}


@dataclass(frozen=True, slots=True)
class SetupRecoveryEntry:
    operation_id: str
    revision: int
    state: str
    proposal: WorkspaceMaterializationProposal
    expected_profile_digest: str | None
    core_receipt: dict[str, Any] | None
    record_digest: str
    created_at: str


class SetupRecoveryStore:
    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.root = Path(root)
        self.clock = clock or (lambda: datetime.now(UTC))

    def append(
        self,
        proposal: WorkspaceMaterializationProposal,
        *,
        state: str,
        expected_profile_digest: str | None,
        core_receipt: WorkspaceMaterializationReceipt | None = None,
    ) -> SetupRecoveryEntry:
        if state not in _STATES:
            raise ValueError("setup recovery state is invalid")
        self.root.mkdir(mode=0o700, parents=True, exist_ok=True)
        previous = self.latest(proposal.operation_id, missing_ok=True)
        receipt_payload = None if core_receipt is None else asdict(core_receipt)
        if (
            previous is not None
            and previous.state == state
            and previous.expected_profile_digest == expected_profile_digest
            and previous.core_receipt == receipt_payload
        ):
            return previous
        revision = 1 if previous is None else previous.revision + 1
        payload = {
            "contract_version": SETUP_RECOVERY_CONTRACT,
            "operation_id": proposal.operation_id,
            "revision": revision,
            "state": state,
            "predecessor_digest": None if previous is None else previous.record_digest,
            "proposal": _proposal_to_dict(proposal),
            "expected_profile_digest": expected_profile_digest,
            "core_receipt": receipt_payload,
            "created_at": _timestamp(self.clock()),
        }
        record = {**payload, "record_digest": _digest(payload)}
        path = self.root / f"{proposal.operation_id}-{revision:06d}-{uuid.uuid4().hex}.json"
        _write_new(path, _json_bytes(record))
        return _entry(record)

    def latest(
        self,
        operation_id: str,
        *,
        missing_ok: bool = False,
    ) -> SetupRecoveryEntry | None:
        entries = [entry for entry in self.list_current() if entry.operation_id == operation_id]
        if not entries:
            if missing_ok:
                return None
            raise AppOperationError("RKBAPP-SETUP-RECOVERY", "Workspace setup recovery basis is unavailable")
        return max(entries, key=lambda item: (item.revision, item.record_digest))

    def list_current(self) -> tuple[SetupRecoveryEntry, ...]:
        if not self.root.is_dir():
            return ()
        chains: dict[str, list[SetupRecoveryEntry]] = {}
        for path in self.root.glob("operation_*.json"):
            record = _read_record(path)
            entry = _entry(record)
            chains.setdefault(entry.operation_id, []).append(entry)
        current: list[SetupRecoveryEntry] = []
        for operation_id, entries in chains.items():
            ordered = sorted(entries, key=lambda item: item.revision)
            if [item.revision for item in ordered] != list(range(1, len(ordered) + 1)):
                raise AppOperationError("RKBAPP-SETUP-RECOVERY", "Workspace setup recovery revisions are incomplete")
            predecessor: str | None = None
            for entry in ordered:
                raw = _read_record(_path_for_digest(self.root, operation_id, entry.record_digest))
                if raw["predecessor_digest"] != predecessor:
                    raise AppOperationError("RKBAPP-SETUP-RECOVERY", "Workspace setup recovery chain is invalid")
                predecessor = entry.record_digest
            current.append(ordered[-1])
        return tuple(sorted(current, key=lambda item: item.operation_id))


def _path_for_digest(root: Path, operation_id: str, digest: str) -> Path:
    matches = []
    for path in root.glob(f"{operation_id}-*.json"):
        record = _read_record(path)
        if record.get("record_digest") == digest:
            matches.append(path)
    if len(matches) != 1:
        raise AppOperationError("RKBAPP-SETUP-RECOVERY", "Workspace setup recovery identity is ambiguous")
    return matches[0]


def _proposal_to_dict(proposal: WorkspaceMaterializationProposal) -> dict[str, Any]:
    request = proposal.request
    return {
        "protocol": proposal.protocol,
        "workspace_id": proposal.workspace_id,
        "domain_profile_id": proposal.domain_profile_id,
        "proposal_id": proposal.proposal_id,
        "operation_id": proposal.operation_id,
        "target": str(proposal.target),
        "request": {
            "workspace_parent": str(request.workspace_parent),
            "workspace_name": request.workspace_name,
            "workspace_label": request.workspace_label,
            "source_roots": [
                {"root_id": item.root_id, "path": str(item.path)} for item in request.source_roots
            ],
            "local_inbox": str(request.local_inbox),
            "idempotency_key": request.idempotency_key,
            "expires_at": proposal.expires_at,
        },
        "parent_attestation": proposal.parent_attestation.to_dict(),
        "source_root_attestations": [item.to_dict() for item in proposal.source_root_attestations],
        "local_inbox_attestation": proposal.local_inbox_attestation.to_dict(),
        "workspace_config": proposal.workspace_config,
        "domain_profile": proposal.domain_profile,
        "preview": proposal.preview,
        "proposal_digest": proposal.proposal_digest,
        "preview_digest": proposal.preview_digest,
        "expires_at": proposal.expires_at,
    }


def _proposal_from_dict(value: Any) -> WorkspaceMaterializationProposal:
    if not isinstance(value, dict):
        raise AppOperationError("RKBAPP-SETUP-RECOVERY", "Workspace setup proposal record is invalid")
    request = value["request"]
    proposal = WorkspaceMaterializationProposal(
        protocol=value["protocol"],
        workspace_id=value["workspace_id"],
        domain_profile_id=value["domain_profile_id"],
        proposal_id=value["proposal_id"],
        operation_id=value["operation_id"],
        target=Path(value["target"]),
        request=WorkspaceMaterializationRequest(
            workspace_parent=Path(request["workspace_parent"]),
            workspace_name=request["workspace_name"],
            workspace_label=request["workspace_label"],
            source_roots=tuple(
                ExternalSourceRoot(item["root_id"], Path(item["path"])) for item in request["source_roots"]
            ),
            local_inbox=Path(request["local_inbox"]),
            idempotency_key=request["idempotency_key"],
            expires_at=_parse_timestamp(request["expires_at"]),
        ),
        parent_attestation=RootSecurityAttestation(**value["parent_attestation"]),
        source_root_attestations=tuple(
            RootSecurityAttestation(**item) for item in value["source_root_attestations"]
        ),
        local_inbox_attestation=RootSecurityAttestation(**value["local_inbox_attestation"]),
        workspace_config=value["workspace_config"],
        domain_profile=value["domain_profile"],
        preview=value["preview"],
        proposal_digest=value["proposal_digest"],
        preview_digest=value["preview_digest"],
        expires_at=value["expires_at"],
    )
    return proposal


def _entry(record: dict[str, Any]) -> SetupRecoveryEntry:
    return SetupRecoveryEntry(
        operation_id=record["operation_id"],
        revision=record["revision"],
        state=record["state"],
        proposal=_proposal_from_dict(record["proposal"]),
        expected_profile_digest=record["expected_profile_digest"],
        core_receipt=record["core_receipt"],
        record_digest=record["record_digest"],
        created_at=record["created_at"],
    )


def _read_record(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise AppOperationError("RKBAPP-SETUP-RECOVERY", "Workspace setup recovery record is unreadable") from error
    if (
        not isinstance(value, dict)
        or value.get("contract_version") != SETUP_RECOVERY_CONTRACT
        or value.get("state") not in _STATES
        or type(value.get("revision")) is not int
        or value["revision"] < 1
    ):
        raise AppOperationError("RKBAPP-SETUP-RECOVERY", "Workspace setup recovery record is invalid")
    digest = value.get("record_digest")
    payload = {key: item for key, item in value.items() if key != "record_digest"}
    if not isinstance(digest, str) or _digest(payload) != digest:
        raise AppOperationError("RKBAPP-SETUP-RECOVERY", "Workspace setup recovery digest is invalid")
    return value


def _write_new(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _json_bytes(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


def _digest(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value).rstrip(b"\n")).hexdigest()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("setup recovery clock must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


__all__ = ["SETUP_RECOVERY_CONTRACT", "SetupRecoveryEntry", "SetupRecoveryStore"]
