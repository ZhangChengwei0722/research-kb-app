from __future__ import annotations

import json
import os
import stat
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any


CONFIG_CONTRACT_V1_0 = "research-kb-app-config@1.0"
CONFIG_CONTRACT = "research-kb-app-config@1.1"
TOP_LEVEL_KEYS_V1_0 = {
    "contract_version",
    "workspaces",
    "state_root",
    "log_root",
    "frontend_root",
    "request_budgets",
}
TOP_LEVEL_KEYS_V1_1 = TOP_LEVEL_KEYS_V1_0 | {"obsidian_targets"}
WORKSPACE_KEYS = {"option_id", "label", "config_path"}
OBSIDIAN_TARGET_KEYS = {
    "target_id",
    "label",
    "workspace_option_id",
    "vault_root",
    "managed_subtree",
    "personal_notes_subtree",
}
BUDGET_KEYS = {"max_body_bytes", "max_query_bytes", "max_page_size", "request_timeout_seconds"}
_WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


class AppConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class WorkspaceOption:
    option_id: str
    label: str
    config_path: Path


@dataclass(frozen=True, slots=True)
class RequestBudgets:
    max_body_bytes: int
    max_query_bytes: int
    max_page_size: int
    request_timeout_seconds: float


@dataclass(frozen=True, slots=True)
class ObsidianTarget:
    target_id: str
    label: str
    workspace_option_id: str
    vault_root: Path
    managed_subtree: PurePosixPath
    personal_notes_subtree: PurePosixPath

    @property
    def managed_root(self) -> Path:
        return self.vault_root.joinpath(*self.managed_subtree.parts)

    @property
    def personal_notes_root(self) -> Path:
        return self.vault_root.joinpath(*self.personal_notes_subtree.parts)


@dataclass(frozen=True, slots=True)
class AppConfig:
    path: Path
    workspaces: tuple[WorkspaceOption, ...]
    state_root: Path
    log_root: Path
    frontend_root: Path
    request_budgets: RequestBudgets
    obsidian_targets: tuple[ObsidianTarget, ...] = ()

    def workspace_mapping(self) -> dict[str, Path]:
        return {option.option_id: option.config_path for option in self.workspaces}

    def labels(self) -> dict[str, str]:
        return {option.option_id: option.label for option in self.workspaces}

    def obsidian_target_mapping(self) -> dict[str, ObsidianTarget]:
        return {target.target_id: target for target in self.obsidian_targets}

    def obsidian_targets_for(self, workspace_option_id: str) -> tuple[ObsidianTarget, ...]:
        return tuple(
            target
            for target in self.obsidian_targets
            if target.workspace_option_id == workspace_option_id
        )


def load_app_config(path: Path) -> AppConfig:
    candidate = Path(path)
    if _is_remote_path(candidate):
        raise AppConfigError("App configuration path must be local")
    config_path = candidate.resolve()
    if not config_path.is_file():
        raise AppConfigError("App configuration is not a regular file")
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AppConfigError("App configuration has unknown or missing keys")
    contract = payload.get("contract_version")
    expected_keys = {
        CONFIG_CONTRACT_V1_0: TOP_LEVEL_KEYS_V1_0,
        CONFIG_CONTRACT: TOP_LEVEL_KEYS_V1_1,
    }.get(contract)
    if expected_keys is None:
        raise AppConfigError("App configuration contract version is incompatible")
    if set(payload) != expected_keys:
        raise AppConfigError("App configuration has unknown or missing keys")

    raw_workspaces = payload["workspaces"]
    if not isinstance(raw_workspaces, list) or not raw_workspaces:
        raise AppConfigError("At least one configured workspace is required")
    options = tuple(_load_workspace_option(item) for item in raw_workspaces)
    option_ids = [option.option_id for option in options]
    if len(option_ids) != len(set(option_ids)):
        raise AppConfigError("Workspace option IDs must be unique")

    state_root = _absolute_path(payload["state_root"], "state_root")
    log_root = _absolute_path(payload["log_root"], "log_root")
    frontend_root = _absolute_path(payload["frontend_root"], "frontend_root")
    if not (log_root == state_root or log_root.is_relative_to(state_root)):
        raise AppConfigError("log_root must be contained by state_root")
    if _has_unsafe_component(frontend_root):
        raise AppConfigError("frontend_root traverses an unsafe filesystem link")
    if not (frontend_root / "index.html").is_file():
        raise AppConfigError("frontend_root does not contain a production index")

    budgets = _load_budgets(payload["request_budgets"])
    targets = (
        ()
        if contract == CONFIG_CONTRACT_V1_0
        else _load_obsidian_targets(
            payload["obsidian_targets"],
            workspace_option_ids=set(option_ids),
        )
    )
    _validate_target_root_separation(
        targets,
        config_path=config_path,
        workspace_options=options,
        state_root=state_root,
        frontend_root=frontend_root,
    )
    return AppConfig(
        path=config_path,
        workspaces=options,
        state_root=state_root,
        log_root=log_root,
        frontend_root=frontend_root,
        request_budgets=budgets,
        obsidian_targets=targets,
    )


def _load_workspace_option(value: Any) -> WorkspaceOption:
    if not isinstance(value, dict) or set(value) != WORKSPACE_KEYS:
        raise AppConfigError("Workspace option has unknown or missing keys")
    if not isinstance(value["option_id"], str) or not value["option_id"]:
        raise AppConfigError("Workspace option ID is invalid")
    if not isinstance(value["label"], str) or not value["label"].strip():
        raise AppConfigError("Workspace label is invalid")
    return WorkspaceOption(
        option_id=value["option_id"],
        label=value["label"].strip(),
        config_path=_absolute_path(value["config_path"], "workspace config_path"),
    )


def _load_budgets(value: Any) -> RequestBudgets:
    if not isinstance(value, dict) or set(value) != BUDGET_KEYS:
        raise AppConfigError("Request budgets have unknown or missing keys")
    integers = ("max_body_bytes", "max_query_bytes", "max_page_size")
    if any(type(value[key]) is not int for key in integers):
        raise AppConfigError("Request integer budgets are invalid")
    timeout = value["request_timeout_seconds"]
    if type(timeout) not in {int, float}:
        raise AppConfigError("Request timeout budget is invalid")
    if not 1024 <= value["max_body_bytes"] <= 1024 * 1024:
        raise AppConfigError("max_body_bytes is outside the supported range")
    if not 128 <= value["max_query_bytes"] <= 16 * 1024:
        raise AppConfigError("max_query_bytes is outside the supported range")
    if not 1 <= value["max_page_size"] <= 100:
        raise AppConfigError("max_page_size is outside the supported range")
    if not 1 <= float(timeout) <= 120:
        raise AppConfigError("request_timeout_seconds is outside the supported range")
    return RequestBudgets(
        max_body_bytes=value["max_body_bytes"],
        max_query_bytes=value["max_query_bytes"],
        max_page_size=value["max_page_size"],
        request_timeout_seconds=float(timeout),
    )


def _load_obsidian_targets(
    value: Any,
    *,
    workspace_option_ids: set[str],
) -> tuple[ObsidianTarget, ...]:
    if not isinstance(value, list):
        raise AppConfigError("obsidian_targets must be a list")
    targets = tuple(
        _load_obsidian_target(item, workspace_option_ids=workspace_option_ids)
        for item in value
    )
    target_ids = [target.target_id for target in targets]
    if len(target_ids) != len(set(target_ids)):
        raise AppConfigError("Obsidian target IDs must be unique")
    return targets


def _load_obsidian_target(
    value: Any,
    *,
    workspace_option_ids: set[str],
) -> ObsidianTarget:
    if not isinstance(value, dict) or set(value) != OBSIDIAN_TARGET_KEYS:
        raise AppConfigError("Obsidian target has unknown or missing keys")
    target_id = value["target_id"]
    if not _browser_id(target_id):
        raise AppConfigError("Obsidian target ID is invalid")
    label = value["label"]
    if not isinstance(label, str) or not label.strip():
        raise AppConfigError("Obsidian target label is invalid")
    workspace_option_id = value["workspace_option_id"]
    if workspace_option_id not in workspace_option_ids:
        raise AppConfigError("Obsidian target workspace option is unknown")
    vault_root = _absolute_path(value["vault_root"], "Obsidian vault_root")
    if not vault_root.is_dir():
        raise AppConfigError("Obsidian vault_root must be an existing directory")
    managed = _relative_subtree(value["managed_subtree"], "managed_subtree")
    personal = _relative_subtree(value["personal_notes_subtree"], "personal_notes_subtree")
    if _relative_paths_overlap(managed, personal):
        raise AppConfigError("Obsidian managed and personal subtrees must not overlap")
    target = ObsidianTarget(
        target_id=target_id,
        label=label.strip(),
        workspace_option_id=workspace_option_id,
        vault_root=vault_root,
        managed_subtree=managed,
        personal_notes_subtree=personal,
    )
    for path in (target.managed_root, target.personal_notes_root):
        if _has_unsafe_component(path):
            raise AppConfigError("Obsidian target subtree traverses an unsafe filesystem link")
        if path.exists() and not path.is_dir():
            raise AppConfigError("Obsidian target subtree is not a directory")
        if not path.resolve().is_relative_to(vault_root):
            raise AppConfigError("Obsidian target subtree escapes its vault")
    return target


def _relative_subtree(value: Any, label: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value or ":" in value:
        raise AppConfigError(f"Obsidian {label} is invalid")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise AppConfigError(f"Obsidian {label} must be a confined relative POSIX path")
    for part in path.parts:
        normalized = part.rstrip(". ")
        if normalized != part or normalized.upper() in _WINDOWS_RESERVED_NAMES:
            raise AppConfigError(f"Obsidian {label} contains a reserved component")
    return path


def _relative_paths_overlap(left: PurePosixPath, right: PurePosixPath) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _validate_target_root_separation(
    targets: tuple[ObsidianTarget, ...],
    *,
    config_path: Path,
    workspace_options: tuple[WorkspaceOption, ...],
    state_root: Path,
    frontend_root: Path,
) -> None:
    protected_directories = (
        state_root,
        frontend_root,
        *(option.config_path.parent for option in workspace_options),
    )
    protected_files = (config_path, *(option.config_path for option in workspace_options))
    for target in targets:
        for root in (target.managed_root, target.personal_notes_root):
            if any(_paths_overlap(root, protected) for protected in protected_directories):
                raise AppConfigError(
                    "Obsidian target subtree overlaps an App-managed or workspace root"
                )
            if any(file.is_relative_to(root) for file in protected_files):
                raise AppConfigError("Obsidian target subtree contains a protected config file")


def _paths_overlap(left: Path, right: Path) -> bool:
    return left == right or left.is_relative_to(right) or right.is_relative_to(left)


def _browser_id(value: Any) -> bool:
    return (
        isinstance(value, str)
        and 1 <= len(value) <= 64
        and value[0].isalnum()
        and all(character.isalnum() or character in {"-", "_"} for character in value)
    )


def _absolute_path(value: Any, label: str) -> Path:
    if not isinstance(value, str) or not value:
        raise AppConfigError(f"{label} is invalid")
    path = Path(value)
    if not path.is_absolute():
        raise AppConfigError(f"{label} must be absolute")
    if _is_remote_path(path):
        raise AppConfigError(f"{label} must be local")
    if _has_unsafe_component(path):
        raise AppConfigError(f"{label} traverses an unsafe filesystem link")
    return path.resolve()


def _is_remote_path(path: Path) -> bool:
    return str(path).startswith("\\\\")


def _has_unsafe_component(path: Path) -> bool:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if os.path.lexists(current) and _is_unsafe_link(current):
            return True
    return False


def _is_unsafe_link(path: Path) -> bool:
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


__all__ = [
    "AppConfig",
    "AppConfigError",
    "CONFIG_CONTRACT",
    "CONFIG_CONTRACT_V1_0",
    "ObsidianTarget",
    "RequestBudgets",
    "WorkspaceOption",
    "load_app_config",
]
