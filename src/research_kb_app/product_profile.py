from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

import ctypes
from ctypes import wintypes
from research_kb.workspace_materialization import ROOT_SECURITY_POLICY

from research_kb_app.config import AppConfig, ObsidianTarget, RequestBudgets, WorkspaceOption
from research_kb_app.errors import AppOperationError


PROFILE_CONTRACT = "research-kb-app-profile@1.0"
PROFILE_POINTER_CONTRACT = "research-kb-app-profile-pointer@1.0"
_PROFILE_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_REVISION_ID = re.compile(r"^profile-rev-[0-9a-f]{32}$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPTION_ID = re.compile(r"^[a-z][a-z0-9-]{0,63}$")
_TARGET_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_PROFILE_KEYS = {
    "contract_version",
    "profile_id",
    "revision_id",
    "revision",
    "predecessor_revision_id",
    "predecessor_digest",
    "created_at",
    "workspaces",
    "obsidian_targets",
    "request_budgets",
    "record_digest",
}
_POINTER_KEYS = {"contract_version", "profile_id", "revision_id", "record_digest", "pointer_digest"}
_FOLDERID_LOCAL_APP_DATA = uuid.UUID("f1b32785-6fba-4fcf-9d55-7b8e7f157091")


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", wintypes.BYTE * 8),
    ]

    @classmethod
    def from_uuid(cls, value: uuid.UUID) -> "GUID":
        fields = value.fields
        return cls(fields[0], fields[1], fields[2], (wintypes.BYTE * 8)(*value.bytes[8:]))


@dataclass(frozen=True, slots=True)
class ProfileCommit:
    profile_id: str
    revision_id: str
    revision: int
    record_digest: str
    result: str


@dataclass(frozen=True, slots=True)
class ProfileRecovery:
    state: str
    current_revision_id: str | None
    recoverable_revision_ids: tuple[str, ...]


class ManagedProductProfileStore:
    def __init__(
        self,
        profile_root: Path,
        profile_id: str,
        *,
        clock: Callable[[], datetime] | None = None,
        phase_hook: Callable[[str], None] | None = None,
    ) -> None:
        if not _PROFILE_ID.fullmatch(profile_id):
            raise ValueError("managed profile ID is invalid")
        self.profile_root = Path(profile_root).resolve(strict=False)
        self.profile_id = profile_id
        self.clock = clock or (lambda: datetime.now(UTC))
        self.phase_hook = phase_hook

    @property
    def config_root(self) -> Path:
        return self.profile_root / "config"

    @property
    def state_root(self) -> Path:
        return self.profile_root / "state"

    @property
    def log_root(self) -> Path:
        return self.profile_root / "logs"

    @property
    def runtime_root(self) -> Path:
        return self.profile_root / "runtime"

    @property
    def receipts_root(self) -> Path:
        return self.profile_root / "receipts"

    @property
    def current_pointer_path(self) -> Path:
        return self.config_root / "current.json"

    def ensure_layout(self) -> None:
        if not self.profile_root.is_dir():
            raise AppOperationError("RKBAPP-PROFILE-ROOT", "Managed profile root is unavailable")
        for path in (self.config_root, self.state_root, self.log_root, self.runtime_root, self.receipts_root):
            path.mkdir(mode=0o700, exist_ok=True)

    def commit(
        self,
        *,
        workspaces: Sequence[WorkspaceOption],
        obsidian_targets: Sequence[ObsidianTarget],
        request_budgets: RequestBudgets,
        expected_current_digest: str | None,
    ) -> ProfileCommit:
        self.ensure_layout()
        current = self.load_current(missing_ok=True)
        current_digest = None if current is None else current["record_digest"]
        if current_digest != expected_current_digest:
            raise AppOperationError("RKBAPP-PROFILE-STALE", "Managed profile changed before approval")
        content = _profile_content(workspaces, obsidian_targets, request_budgets)
        if current is not None and _record_content(current) == content:
            return ProfileCommit(
                self.profile_id,
                current["revision_id"],
                current["revision"],
                current["record_digest"],
                "no_change",
            )

        revision = 1 if current is None else current["revision"] + 1
        predecessor_revision_id = None if current is None else current["revision_id"]
        predecessor_digest = None if current is None else current["record_digest"]
        revision_basis = {
            "profile_id": self.profile_id,
            "revision": revision,
            "predecessor_digest": predecessor_digest,
            "content": content,
        }
        revision_id = f"profile-rev-{_digest(revision_basis)[:32]}"
        record = {
            "contract_version": PROFILE_CONTRACT,
            "profile_id": self.profile_id,
            "revision_id": revision_id,
            "revision": revision,
            "predecessor_revision_id": predecessor_revision_id,
            "predecessor_digest": predecessor_digest,
            "created_at": _timestamp(self.clock()),
            **content,
        }
        record = {**record, "record_digest": _digest(record)}
        revision_path = self.config_root / f"{revision_id}.json"
        if revision_path.exists():
            existing = _read_json(revision_path)
            _validate_record(existing, profile_id=self.profile_id)
            self._validate_predecessor(existing)
            if _record_content(existing) != content or existing["revision"] != revision:
                raise AppOperationError("RKBAPP-PROFILE-CONFLICT", "Managed profile revision identity is already in use")
            record = existing
        else:
            _write_new(revision_path, _json_bytes(record))
        self._phase("revision_persisted")
        self._write_pointer(record)
        self._phase("pointer_replaced")
        return ProfileCommit(self.profile_id, revision_id, revision, record["record_digest"], "created")

    def load_current(self, *, missing_ok: bool = False) -> dict[str, Any] | None:
        pointer_path = self.current_pointer_path
        if not pointer_path.is_file():
            if missing_ok:
                return None
            raise AppOperationError("RKBAPP-PROFILE-CURRENT", "Managed profile has no current revision")
        pointer = _read_json(pointer_path)
        _validate_pointer(pointer, profile_id=self.profile_id)
        revision_path = self.config_root / f"{pointer['revision_id']}.json"
        record = _read_json(revision_path)
        _validate_record(record, profile_id=self.profile_id)
        self._validate_predecessor(record)
        if record["record_digest"] != pointer["record_digest"]:
            raise AppOperationError("RKBAPP-PROFILE-CURRENT", "Managed profile pointer does not match its revision")
        return record

    def inspect_recovery(self) -> ProfileRecovery:
        records = self._valid_revisions()
        try:
            current = self.load_current(missing_ok=True)
        except (AppOperationError, OSError, ValueError, json.JSONDecodeError):
            current = None
            state = "current_invalid"
        else:
            state = "current" if current is not None else "current_missing"
        current_id = None if current is None else current["revision_id"]
        recoverable = tuple(
            record["revision_id"]
            for record in sorted(records, key=lambda item: (item["revision"], item["revision_id"]), reverse=True)
            if record["revision_id"] != current_id
        )
        return ProfileRecovery(state, current_id, recoverable)

    def select_recovery_revision(self, revision_id: str) -> ProfileCommit:
        if not _REVISION_ID.fullmatch(revision_id):
            raise AppOperationError("RKBAPP-PROFILE-RECOVERY", "Managed profile revision ID is invalid", status_code=400)
        record = _read_json(self.config_root / f"{revision_id}.json")
        _validate_record(record, profile_id=self.profile_id)
        self._validate_predecessor(record)
        self._write_pointer(record)
        return ProfileCommit(
            self.profile_id,
            record["revision_id"],
            record["revision"],
            record["record_digest"],
            "selected",
        )

    def rollback_current_if_matches(
        self,
        failed_commit: ProfileCommit,
        *,
        predecessor_revision_id: str | None,
    ) -> None:
        current = self.load_current()
        assert current is not None
        if (
            current["revision_id"] != failed_commit.revision_id
            or current["record_digest"] != failed_commit.record_digest
        ):
            raise AppOperationError(
                "RKBAPP-PROFILE-ROLLBACK",
                "Managed profile changed before failed validation could be rolled back",
            )
        if predecessor_revision_id is not None:
            self.select_recovery_revision(predecessor_revision_id)
            return
        if current["revision"] != 1 or current["predecessor_revision_id"] is not None:
            raise AppOperationError(
                "RKBAPP-PROFILE-ROLLBACK",
                "Managed profile has no valid empty predecessor",
            )
        self.current_pointer_path.unlink()

    def to_app_config(self, *, frontend_root: Path) -> AppConfig:
        current = self.load_current()
        assert current is not None
        budgets = current["request_budgets"]
        return AppConfig(
            path=self.current_pointer_path,
            workspaces=tuple(
                WorkspaceOption(item["option_id"], item["label"], Path(item["config_path"]))
                for item in current["workspaces"]
            ),
            state_root=self.state_root,
            log_root=self.log_root,
            frontend_root=Path(frontend_root).resolve(strict=True),
            request_budgets=RequestBudgets(**budgets),
            obsidian_targets=tuple(
                ObsidianTarget(
                    item["target_id"],
                    item["label"],
                    item["workspace_option_id"],
                    Path(item["vault_root"]),
                    PurePosixPath(item["managed_subtree"]),
                    PurePosixPath(item["personal_notes_subtree"]),
                )
                for item in current["obsidian_targets"]
            ),
        )

    def _valid_revisions(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        if not self.config_root.is_dir():
            return records
        for path in self.config_root.glob("profile-rev-*.json"):
            try:
                record = _read_json(path)
                _validate_record(record, profile_id=self.profile_id)
                self._validate_predecessor(record)
            except (AppOperationError, OSError, ValueError, json.JSONDecodeError):
                continue
            records.append(record)
        return records

    def _write_pointer(self, record: dict[str, Any]) -> None:
        pointer = {
            "contract_version": PROFILE_POINTER_CONTRACT,
            "profile_id": self.profile_id,
            "revision_id": record["revision_id"],
            "record_digest": record["record_digest"],
        }
        pointer = {**pointer, "pointer_digest": _digest(pointer)}
        _atomic_replace(self.current_pointer_path, _json_bytes(pointer))

    def _validate_predecessor(self, record: dict[str, Any]) -> None:
        if record["revision"] == 1:
            return
        predecessor = _read_json(self.config_root / f"{record['predecessor_revision_id']}.json")
        _validate_record(predecessor, profile_id=self.profile_id)
        if (
            predecessor["revision"] != record["revision"] - 1
            or predecessor["record_digest"] != record["predecessor_digest"]
        ):
            raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile predecessor binding is invalid")

    def _phase(self, value: str) -> None:
        if self.phase_hook is not None:
            self.phase_hook(value)


def local_app_data_root() -> Path:
    if os.name != "nt":
        raise AppOperationError("RKBAPP-KNOWN-FOLDER", "Windows Local AppData is unavailable")
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32", use_last_error=True)
    shell32.SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(GUID),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    shell32.SHGetKnownFolderPath.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    ole32.CoTaskMemFree.restype = None
    value = wintypes.LPWSTR()
    folder_id = GUID.from_uuid(_FOLDERID_LOCAL_APP_DATA)
    result = shell32.SHGetKnownFolderPath(ctypes.byref(folder_id), 0, None, ctypes.byref(value))
    if result != 0 or not value.value:
        raise AppOperationError("RKBAPP-KNOWN-FOLDER", "Windows Local AppData could not be resolved")
    try:
        return Path(value.value).resolve(strict=True)
    finally:
        ole32.CoTaskMemFree(ctypes.cast(value, ctypes.c_void_p))


def ensure_managed_profile_root(
    profile_id: str,
    root_security: Any,
    *,
    known_folder_resolver: Callable[[], Path] = local_app_data_root,
) -> Path:
    if not _PROFILE_ID.fullmatch(profile_id):
        raise AppOperationError("RKBAPP-PROFILE-ID", "Managed profile ID is invalid", status_code=400)
    base = known_folder_resolver()
    vendor = base / "ResearchKB"
    profile = vendor / profile_id
    for path, operation_id in (
        (vendor, f"profile-vendor-{profile_id}"),
        (profile, f"profile-root-{profile_id}"),
    ):
        if path.exists():
            attestation = root_security.inspect(path)
            if not (
                attestation.filesystem.upper() == "NTFS"
                and attestation.local
                and attestation.reparse_free
                and attestation.acl_policy_id == ROOT_SECURITY_POLICY
                and attestation.acl_secure
            ):
                raise AppOperationError("RKBAPP-PROFILE-ROOT", "Managed profile root is not secure")
        else:
            root_security.secure_create(path, operation_id=operation_id)
    return profile.resolve(strict=True)


def _profile_content(
    workspaces: Sequence[WorkspaceOption],
    obsidian_targets: Sequence[ObsidianTarget],
    request_budgets: RequestBudgets,
) -> dict[str, Any]:
    workspace_items = [
        {"option_id": item.option_id, "label": item.label, "config_path": str(item.config_path.resolve(strict=True))}
        for item in workspaces
    ]
    workspace_items.sort(key=lambda item: item["option_id"])
    if not workspace_items or len({item["option_id"] for item in workspace_items}) != len(workspace_items):
        raise AppOperationError("RKBAPP-PROFILE-WORKSPACE", "Managed profile workspaces are missing or duplicated")
    target_items = [
        {
            "target_id": item.target_id,
            "label": item.label,
            "workspace_option_id": item.workspace_option_id,
            "vault_root": str(item.vault_root.resolve(strict=True)),
            "managed_subtree": item.managed_subtree.as_posix(),
            "personal_notes_subtree": item.personal_notes_subtree.as_posix(),
        }
        for item in obsidian_targets
    ]
    target_items.sort(key=lambda item: item["target_id"])
    return {
        "workspaces": workspace_items,
        "obsidian_targets": target_items,
        "request_budgets": {
            "max_body_bytes": request_budgets.max_body_bytes,
            "max_query_bytes": request_budgets.max_query_bytes,
            "max_page_size": request_budgets.max_page_size,
            "request_timeout_seconds": request_budgets.request_timeout_seconds,
        },
    }


def _record_content(record: dict[str, Any]) -> dict[str, Any]:
    return {key: record[key] for key in ("workspaces", "obsidian_targets", "request_budgets")}


def _validate_record(record: Any, *, profile_id: str) -> None:
    if not isinstance(record, dict) or set(record) != _PROFILE_KEYS:
        raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile revision is invalid")
    if (
        record["contract_version"] != PROFILE_CONTRACT
        or record["profile_id"] != profile_id
        or not _REVISION_ID.fullmatch(record["revision_id"])
        or type(record["revision"]) is not int
        or record["revision"] < 1
        or not _SHA256.fullmatch(record["record_digest"])
    ):
        raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile revision identity is invalid")
    payload = {key: value for key, value in record.items() if key != "record_digest"}
    if _digest(payload) != record["record_digest"]:
        raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile revision digest is invalid")
    predecessor_valid = (
        record["revision"] == 1
        and record["predecessor_revision_id"] is None
        and record["predecessor_digest"] is None
    ) or (
        record["revision"] > 1
        and isinstance(record["predecessor_revision_id"], str)
        and _REVISION_ID.fullmatch(record["predecessor_revision_id"])
        and isinstance(record["predecessor_digest"], str)
        and _SHA256.fullmatch(record["predecessor_digest"])
    )
    if not predecessor_valid or not _valid_timestamp(record["created_at"]):
        raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile revision lineage is invalid")
    content = _validated_record_content(record)
    revision_basis = {
        "profile_id": profile_id,
        "revision": record["revision"],
        "predecessor_digest": record["predecessor_digest"],
        "content": content,
    }
    expected_revision_id = f"profile-rev-{_digest(revision_basis)[:32]}"
    if record["revision_id"] != expected_revision_id:
        raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile revision basis is invalid")


def _validated_record_content(record: dict[str, Any]) -> dict[str, Any]:
    workspaces = record["workspaces"]
    if not isinstance(workspaces, list) or not workspaces:
        raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile workspace list is invalid")
    option_ids: list[str] = []
    option_paths: list[str] = []
    for item in workspaces:
        if not isinstance(item, dict) or set(item) != {"option_id", "label", "config_path"}:
            raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile workspace entry is invalid")
        if not isinstance(item["option_id"], str) or not _OPTION_ID.fullmatch(item["option_id"]):
            raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile workspace identity is invalid")
        if not isinstance(item["label"], str) or not item["label"].strip() or len(item["label"]) > 200:
            raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile workspace label is invalid")
        if not isinstance(item["config_path"], str) or not Path(item["config_path"]).is_absolute():
            raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile workspace path is invalid")
        option_ids.append(item["option_id"])
        option_paths.append(str(Path(item["config_path"])).casefold())
    if option_ids != sorted(option_ids) or len(option_ids) != len(set(option_ids)) or len(option_paths) != len(set(option_paths)):
        raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile workspaces are duplicated or noncanonical")

    targets = record["obsidian_targets"]
    if not isinstance(targets, list):
        raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile Obsidian targets are invalid")
    target_ids: list[str] = []
    option_id_set = set(option_ids)
    for item in targets:
        if not isinstance(item, dict) or set(item) != {
            "target_id",
            "label",
            "workspace_option_id",
            "vault_root",
            "managed_subtree",
            "personal_notes_subtree",
        }:
            raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile Obsidian target is invalid")
        if not isinstance(item["target_id"], str) or not _TARGET_ID.fullmatch(item["target_id"]):
            raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile Obsidian target identity is invalid")
        if not isinstance(item["label"], str) or not item["label"].strip() or len(item["label"]) > 200:
            raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile Obsidian label is invalid")
        if item["workspace_option_id"] not in option_id_set:
            raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile Obsidian workspace binding is invalid")
        if not isinstance(item["vault_root"], str) or not Path(item["vault_root"]).is_absolute():
            raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile Obsidian root is invalid")
        if not _valid_subtree(item["managed_subtree"]) or not _valid_subtree(item["personal_notes_subtree"]):
            raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile Obsidian subtree is invalid")
        target_ids.append(item["target_id"])
    if target_ids != sorted(target_ids) or len(target_ids) != len(set(target_ids)):
        raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile Obsidian targets are duplicated or noncanonical")

    budgets = record["request_budgets"]
    if not isinstance(budgets, dict) or set(budgets) != {
        "max_body_bytes",
        "max_query_bytes",
        "max_page_size",
        "request_timeout_seconds",
    }:
        raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile request budgets are invalid")
    valid_budgets = (
        type(budgets["max_body_bytes"]) is int
        and 1024 <= budgets["max_body_bytes"] <= 64 * 1024 * 1024
        and type(budgets["max_query_bytes"]) is int
        and 1 <= budgets["max_query_bytes"] <= budgets["max_body_bytes"]
        and type(budgets["max_page_size"]) is int
        and 1 <= budgets["max_page_size"] <= 1000
        and type(budgets["request_timeout_seconds"]) in {int, float}
        and 0 < budgets["request_timeout_seconds"] <= 3600
    )
    if not valid_budgets:
        raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile request budgets are invalid")
    return _record_content(record)


def _valid_subtree(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\\" in value:
        return False
    path = PurePosixPath(value)
    return not path.is_absolute() and all(part not in {"", ".", ".."} for part in path.parts)


def _valid_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == UTC.utcoffset(parsed)


def _validate_pointer(pointer: Any, *, profile_id: str) -> None:
    if not isinstance(pointer, dict) or set(pointer) != _POINTER_KEYS:
        raise AppOperationError("RKBAPP-PROFILE-CURRENT", "Managed profile pointer is invalid")
    payload = {key: value for key, value in pointer.items() if key != "pointer_digest"}
    if (
        pointer["contract_version"] != PROFILE_POINTER_CONTRACT
        or pointer["profile_id"] != profile_id
        or not _REVISION_ID.fullmatch(pointer["revision_id"])
        or not _SHA256.fullmatch(pointer["record_digest"])
        or not _SHA256.fullmatch(pointer["pointer_digest"])
        or _digest(payload) != pointer["pointer_digest"]
    ):
        raise AppOperationError("RKBAPP-PROFILE-CURRENT", "Managed profile pointer identity is invalid")


def _write_new(path: Path, content: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _atomic_replace(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp")
    _write_new(temporary, content)
    try:
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _read_json(path: Path) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile file is unavailable")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AppOperationError("RKBAPP-PROFILE-RECORD", "Managed profile file is invalid")
    return value


def _json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("managed profile clock must be timezone-aware")
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


__all__ = [
    "PROFILE_CONTRACT",
    "ManagedProductProfileStore",
    "ProfileCommit",
    "ProfileRecovery",
    "ensure_managed_profile_root",
    "local_app_data_root",
]
