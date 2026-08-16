from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from tools.public_fixture_contract import (
    FixtureContractError,
    default_fixture_root,
    default_manifest_path,
    verify_fixture,
)


def _copy_fixture(tmp_path: Path) -> Path:
    target = tmp_path / "p2-small"
    shutil.copytree(default_fixture_root(), target)
    return target


def test_vendored_fixture_matches_pinned_contract() -> None:
    verification = verify_fixture(default_fixture_root())

    assert verification.file_count == 25
    assert verification.total_bytes == 32032
    assert verification.whole_tree_digest == "e04f04340d2b19e0596d6e99bfacc4ea9cc85a3ed2bb469463c0bb7e9ccf29e2"


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    fixture = _copy_fixture(tmp_path)
    (fixture / "workspace.yaml").unlink()

    with pytest.raises(FixtureContractError, match="file set mismatch.*missing=workspace.yaml"):
        verify_fixture(fixture)


def test_extra_file_is_rejected(tmp_path: Path) -> None:
    fixture = _copy_fixture(tmp_path)
    (fixture / "extra.txt").write_bytes(b"extra")

    with pytest.raises(FixtureContractError, match="file set mismatch.*extra=extra.txt"):
        verify_fixture(fixture)


def test_mutated_file_is_rejected(tmp_path: Path) -> None:
    fixture = _copy_fixture(tmp_path)
    path = fixture / "sources" / "source-00000001.txt"
    path.write_bytes(path.read_bytes() + b"mutation")

    with pytest.raises(FixtureContractError, match="size mismatch|digest mismatch"):
        verify_fixture(fixture)


def test_symlink_or_reparse_point_is_rejected_when_supported(tmp_path: Path) -> None:
    fixture = _copy_fixture(tmp_path)
    link = fixture / "workspace.yaml"
    link.unlink()
    try:
        link.symlink_to(fixture / "domain-profile.yaml")
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"symlink/reparse creation is unavailable: {exc}")

    with pytest.raises(FixtureContractError, match="symlink/reparse"):
        verify_fixture(fixture)


def test_manifest_traversal_is_rejected(tmp_path: Path) -> None:
    fixture = _copy_fixture(tmp_path)
    manifest = tmp_path / "fixture-manifest.json"
    payload = json.loads(default_manifest_path().read_text(encoding="utf-8"))
    payload["files"][0]["path"] = "../outside.txt"
    manifest.write_text(json.dumps(payload), encoding="utf-8", newline="\n")

    with pytest.raises(FixtureContractError, match="traversal path"):
        verify_fixture(fixture, manifest_path=manifest)


def test_source_provenance_digest_change_is_rejected(tmp_path: Path) -> None:
    fixture = _copy_fixture(tmp_path)
    manifest = tmp_path / "fixture-manifest.json"
    payload = json.loads(default_manifest_path().read_text(encoding="utf-8"))
    payload["source_generator_manifest_sha256"] = "0" * 64
    manifest.write_text(json.dumps(payload), encoding="utf-8", newline="\n")

    with pytest.raises(FixtureContractError, match="source_generator_manifest_sha256"):
        verify_fixture(fixture, manifest_path=manifest)


def test_environment_override_is_verified_by_same_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fixture = _copy_fixture(tmp_path)
    monkeypatch.setenv("RKB_P2_SMALL_FIXTURE", os.fspath(fixture))

    assert verify_fixture(Path(os.environ["RKB_P2_SMALL_FIXTURE"])).file_count == 25
