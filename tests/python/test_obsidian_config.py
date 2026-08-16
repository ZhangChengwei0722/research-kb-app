from __future__ import annotations

import json
from pathlib import Path

import pytest

from research_kb_app.config import AppConfigError, load_app_config


def _v11_payload(app_harness, vault: Path) -> dict:
    payload = json.loads(app_harness.config.path.read_text(encoding="utf-8"))
    payload["contract_version"] = "research-kb-app-config@1.1"
    payload["obsidian_targets"] = [
        {
            "target_id": "synthetic-vault",
            "label": "Synthetic Vault",
            "workspace_option_id": "p2-small",
            "vault_root": str(vault.resolve()),
            "managed_subtree": "Research KB/Generated",
            "personal_notes_subtree": "Research KB/Personal",
        }
    ]
    return payload


def _write_config(tmp_path: Path, payload: dict) -> Path:
    path = tmp_path / "obsidian-app-config.json"
    path.write_text(json.dumps(payload), encoding="utf-8", newline="\n")
    return path


def test_v1_config_remains_readable_with_zero_obsidian_targets(app_harness) -> None:
    assert app_harness.config.obsidian_targets == ()
    assert app_harness.config.obsidian_target_mapping() == {}


def test_v11_loads_confined_targets_without_public_path_projection(
    app_harness,
    tmp_path: Path,
) -> None:
    vault = tmp_path / "synthetic-vault"
    vault.mkdir()
    config = load_app_config(_write_config(tmp_path, _v11_payload(app_harness, vault)))

    target = config.obsidian_target_mapping()["synthetic-vault"]
    assert target.workspace_option_id == "p2-small"
    assert target.managed_root == vault / "Research KB" / "Generated"
    assert target.personal_notes_root == vault / "Research KB" / "Personal"
    assert config.obsidian_targets_for("other-workspace") == ()


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("managed_subtree", "../Generated", "confined relative POSIX"),
        ("managed_subtree", "C:/Generated", "invalid"),
        ("managed_subtree", "Research KB\\Generated", "invalid"),
        ("managed_subtree", "Research KB/CON", "reserved"),
        ("personal_notes_subtree", "Research KB/Generated/Personal", "overlap"),
    ],
)
def test_v11_rejects_unsafe_or_overlapping_subtrees(
    app_harness,
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    vault = tmp_path / "synthetic-vault"
    vault.mkdir()
    payload = _v11_payload(app_harness, vault)
    payload["obsidian_targets"][0][field] = value

    with pytest.raises(AppConfigError, match=message):
        load_app_config(_write_config(tmp_path, payload))


def test_v11_rejects_unknown_workspace_and_duplicate_target_ids(
    app_harness,
    tmp_path: Path,
) -> None:
    vault = tmp_path / "synthetic-vault"
    vault.mkdir()
    payload = _v11_payload(app_harness, vault)
    payload["obsidian_targets"][0]["workspace_option_id"] = "unknown"
    with pytest.raises(AppConfigError, match="workspace option"):
        load_app_config(_write_config(tmp_path, payload))

    payload = _v11_payload(app_harness, vault)
    payload["obsidian_targets"].append(dict(payload["obsidian_targets"][0]))
    with pytest.raises(AppConfigError, match="unique"):
        load_app_config(_write_config(tmp_path, payload))


def test_v11_rejects_existing_unsafe_vault_component(app_harness, tmp_path: Path) -> None:
    vault = tmp_path / "synthetic-vault"
    vault.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (vault / "Research KB").symlink_to(outside, target_is_directory=True)
    except OSError as error:
        pytest.skip(f"directory symlink unavailable: {error}")

    with pytest.raises(AppConfigError, match="unsafe filesystem link"):
        load_app_config(_write_config(tmp_path, _v11_payload(app_harness, vault)))


def test_v11_rejects_target_subtree_inside_configured_workspace_root(
    app_harness,
    tmp_path: Path,
) -> None:
    payload = _v11_payload(app_harness, app_harness.workspace_root)
    payload["obsidian_targets"][0]["managed_subtree"] = "knowledge/generated-views"
    payload["obsidian_targets"][0]["personal_notes_subtree"] = "personal-notes"

    with pytest.raises(AppConfigError, match="App-managed or workspace root"):
        load_app_config(_write_config(tmp_path, payload))
