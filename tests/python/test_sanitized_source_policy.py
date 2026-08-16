from __future__ import annotations

import base64
import json
import subprocess
from pathlib import Path

import pytest

from tools.public_source_contract import (
    PublicSourceError,
    build_manifest,
    materialize,
    materialize_with_manifest,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
POLICY_PATH = REPO_ROOT / "public-source-policy.json"


def _git_init(root: Path) -> None:
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)


def _git_commit(root: Path) -> None:
    subprocess.run(["git", "-C", str(root), "add", "--all"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "-c",
            "user.name=Synthetic Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "--quiet",
            "-m",
            "synthetic source",
        ],
        check=True,
    )


def _policy(root: Path, *, allow: list[str] | None = None) -> Path:
    payload = {
        "allow": allow or ["README.md", "src/**"],
        "contract_version": "research-kb-app-public-source@1",
        "deny": ["docs/private/**"],
        "forbidden_literal_b64": ["QzpcVXNlcnNcMzI1NjNcRG9jdW1lbnRzXENvZGV4"],
        "max_file_bytes": 4096,
        "secret_patterns": ["ghp_[A-Za-z0-9]{30,}"],
    }
    path = root / "policy.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_checked_in_policy_classifies_the_current_candidate() -> None:
    manifest = build_manifest(REPO_ROOT, POLICY_PATH)
    assert manifest["contract_version"] == "research-kb-app-public-source@1"
    assert manifest["file_count"] > 100
    assert manifest["byte_count"] > 0
    assert len(manifest["tree_sha256"]) == 64
    paths = {entry["path"] for entry in manifest["files"]}
    assert "README.md" in paths
    assert "docs/receipts/p11-cleanup-dry-run-20260805.json" not in paths


def test_unclassified_file_fails_closed(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "README.md").write_text("public\n", encoding="utf-8")
    (tmp_path / "unknown.txt").write_text("unknown\n", encoding="utf-8")
    policy = _policy(tmp_path)
    with pytest.raises(PublicSourceError, match="unclassified source paths"):
        build_manifest(tmp_path, policy)


def test_private_literal_fails_closed(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / "README.md").write_text(
        base64.b64decode("QzpcVXNlcnNcMzI1NjNcRG9jdW1lbnRzXENvZGV4").decode("utf-8")
        + "\\secret\n",
        encoding="utf-8",
    )
    policy = _policy(tmp_path, allow=["README.md", "policy.json"])
    with pytest.raises(PublicSourceError, match="forbidden private literal"):
        build_manifest(tmp_path, policy)


def test_materialization_is_create_only_and_matches_digest(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git_init(source)
    (source / "README.md").write_text("public\n", encoding="utf-8")
    policy = _policy(source, allow=["README.md", "policy.json"])
    _git_commit(source)
    output = tmp_path / "output"
    manifest = materialize(source, policy, output)
    assert (output / "README.md").read_text(encoding="utf-8") == "public\n"
    assert manifest["file_count"] == 2
    with pytest.raises(PublicSourceError, match="already exists"):
        materialize(source, policy, output)


def test_symlink_is_rejected_when_supported(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git_init(source)
    target = source / "target.txt"
    target.write_text("public\n", encoding="utf-8")
    linked = source / "README.md"
    try:
        linked.symlink_to(target)
    except OSError:
        pytest.skip("symlink creation is unavailable on this Windows profile")
    policy = _policy(source, allow=["README.md", "policy.json", "target.txt"])
    _git_commit(source)
    with pytest.raises(PublicSourceError, match="not a regular file"):
        build_manifest(source, policy)


def test_materialized_tree_has_no_git_history(tmp_path: Path) -> None:
    output = tmp_path / "preview"
    manifest = materialize(REPO_ROOT, POLICY_PATH, output)
    assert not (output / ".git").exists()
    assert manifest["source_head"]
    assert len(list(output.rglob("*"))) >= manifest["file_count"]


def test_existing_manifest_blocks_tree_creation(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git_init(source)
    (source / "README.md").write_text("public\n", encoding="utf-8")
    policy = _policy(source, allow=["README.md", "policy.json"])
    _git_commit(source)
    output = tmp_path / "output"
    manifest = tmp_path / "manifest.json"
    manifest.write_text("existing\n", encoding="utf-8")

    with pytest.raises(PublicSourceError, match="manifest output already exists"):
        materialize_with_manifest(source, policy, output, manifest)
    assert not output.exists()


def test_manifest_cannot_be_inside_materialized_tree(tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    _git_init(source)
    (source / "README.md").write_text("public\n", encoding="utf-8")
    policy = _policy(source, allow=["README.md", "policy.json"])
    _git_commit(source)
    output = tmp_path / "output"

    with pytest.raises(PublicSourceError, match="outside the materialized tree"):
        materialize_with_manifest(source, policy, output, output / "manifest.json")
    assert not output.exists()
