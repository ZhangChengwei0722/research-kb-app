from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from tools.publication_governance import (
    AUTHORITY_SCHEMA,
    GovernanceInputError,
    build_publication_activation,
    build_publication_manifests,
    canonical_json,
    main,
    sha256_file,
    verify_publication_authority,
)

VERSION = "0.1.1b1"
TAG = "v0.1.1b1"


def _files(tmp_path: Path) -> tuple[Path, Path, str, str]:
    wheel = tmp_path / f"research_kb_app-{VERSION}-py3-none-any.whl"
    sdist = tmp_path / f"research_kb_app-{VERSION}.tar.gz"
    wheel.write_bytes(b"synthetic wheel\n")
    sdist.write_bytes(b"synthetic sdist\n")
    return wheel, sdist, sha256_file(wheel), sha256_file(sdist)


def _base(tmp_path: Path):
    wheel, sdist, wheel_digest, sdist_digest = _files(tmp_path)
    return wheel, sdist, dict(
        repository="ZhangChengwei0722/research-kb-app",
        source_commit="f17254f0fc4bb03b92f581122ea9567d4bf15f12",
        workflow_run_id="31934196235",
        workflow_run_attempt="1",
        version=VERSION,
        artifact_id="9260234103",
        artifact_service_digest="28df1a18c78d063247fb37a182dd185cf1f65cd41ab81ebbe958584a5aca349a",
        wheel_sha256=wheel_digest,
        sdist_sha256=sdist_digest,
        tag=TAG,
        actor_id="237524179",
        workflow_execution_commit="f17254f0fc4bb03b92f581122ea9567d4bf15f12",
        workflow_file_sha256="a" * 64,
        branch_protection_preflight_receipt_sha256="b" * 64,
        observed_branch="main",
        observed_at="2026-08-16T07:33:52Z",
        trusted_owner="ZhangChengwei0722",
        trusted_repository="research-kb-app",
        trusted_workflow="publish-accepted-release.yml",
        trusted_environment="pypi",
    )


def _verify(authority):
    publication = authority["publication"]
    context = dict(
        repository="ZhangChengwei0722/research-kb-app",
        actor_id="237524179",
        accepted_run_id="31934196235",
        accepted_run_attempt="1",
        accepted_commit="f17254f0fc4bb03b92f581122ea9567d4bf15f12",
        accepted_artifact_id="9260234103",
        accepted_artifact_service_digest="28df1a18c78d063247fb37a182dd185cf1f65cd41ab81ebbe958584a5aca349a",
        tag=TAG,
        workflow_ref="refs/heads/main",
        environment="pypi",
        workflow_execution_commit="f17254f0fc4bb03b92f581122ea9567d4bf15f12",
        workflow_file_sha256="a" * 64,
        branch_protection_preflight_receipt_sha256="b" * 64,
        observed_branch="main",
        observed_at="2026-08-16T07:33:52Z",
        trusted_owner=publication["trusted_publisher"]["owner"],
        trusted_repository=publication["trusted_publisher"]["repository"],
        trusted_workflow=publication["trusted_publisher"]["workflow"],
        trusted_environment=publication["trusted_publisher"]["environment"],
    )
    return verify_publication_authority(authority, **context)


def test_manifests_are_canonical_and_activate_exact_bytes(tmp_path: Path) -> None:
    wheel, sdist, kwargs = _base(tmp_path)
    authority, activation = build_publication_manifests(**kwargs)
    assert authority["schema_version"] == AUTHORITY_SCHEMA
    assert authority["immutable"] is True
    assert canonical_json(authority) == json.dumps(authority, separators=(",", ":"), sort_keys=True)
    ok, errors = _verify(authority)
    assert ok, errors
    activated = build_publication_activation(authority, wheel_path=wheel, sdist_path=sdist, downloaded_manifest={})
    assert activated["activation"]["authority_manifest_sha256"]
    assert activated["activation"]["accepted_artifact_digests"] == authority["publication"]["accepted_artifact_digests"]


def test_authority_context_drift_fails_closed(tmp_path: Path) -> None:
    kwargs = _base(tmp_path)[2]
    authority, _ = build_publication_manifests(**kwargs)
    ok, errors = _verify(authority)
    assert ok, errors
    publication = authority["publication"]
    context = dict(
        repository="ZhangChengwei0722/research-kb-app",
        actor_id="237524179",
        accepted_run_id="31934196235",
        accepted_run_attempt="1",
        accepted_commit="f17254f0fc4bb03b92f581122ea9567d4bf15f12",
        accepted_artifact_id="9260234103",
        accepted_artifact_service_digest="28df1a18c78d063247fb37a182dd185cf1f65cd41ab81ebbe958584a5aca349a",
        tag=TAG,
        workflow_ref="refs/heads/main",
        environment="pypi",
        workflow_execution_commit="f17254f0fc4bb03b92f581122ea9567d4bf15f12",
        workflow_file_sha256="a" * 64,
        branch_protection_preflight_receipt_sha256="b" * 64,
        observed_branch="main",
        observed_at="2026-08-16T07:33:52Z",
        trusted_owner=publication["trusted_publisher"]["owner"],
        trusted_repository=publication["trusted_publisher"]["repository"],
        trusted_workflow=publication["trusted_publisher"]["workflow"],
        trusted_environment=publication["trusted_publisher"]["environment"],
    )
    context["workflow_execution_commit"] = "0" * 40
    ok, errors = verify_publication_authority(authority, **context)
    assert not ok
    assert any("workflow_execution_commit" in error for error in errors)


def test_stable_version_and_tag_are_rejected(tmp_path: Path) -> None:
    kwargs = _base(tmp_path)[2]
    with pytest.raises(GovernanceInputError, match="pre-release|stable"):
        build_publication_manifests(**{**kwargs, "version": "0.1.1", "tag": "v0.1.1"})


def test_wheel_sdist_substitution_fails_closed(tmp_path: Path) -> None:
    wheel, sdist, kwargs = _base(tmp_path)
    authority, _ = build_publication_manifests(**kwargs)
    wheel.write_bytes(b"different bytes\n")
    with pytest.raises(GovernanceInputError, match="wheel bytes differ"):
        build_publication_activation(authority, wheel_path=wheel, sdist_path=sdist, downloaded_manifest={})


def test_unknown_authority_fields_are_rejected(tmp_path: Path) -> None:
    kwargs = _base(tmp_path)[2]
    authority, _ = build_publication_manifests(**kwargs)
    mutated = copy.deepcopy(authority)
    mutated["extra"] = "not allowed"
    ok, errors = verify_publication_authority(
        mutated,
        **_verify_context(mutated),
    )
    assert not ok
    assert any("canonical" in error for error in errors)


def _verify_context(authority):
    publication = authority["publication"]
    return dict(
        repository="ZhangChengwei0722/research-kb-app",
        actor_id="237524179",
        accepted_run_id="31934196235",
        accepted_run_attempt="1",
        accepted_commit="f17254f0fc4bb03b92f581122ea9567d4bf15f12",
        accepted_artifact_id="9260234103",
        accepted_artifact_service_digest="28df1a18c78d063247fb37a182dd185cf1f65cd41ab81ebbe958584a5aca349a",
        tag=TAG,
        workflow_ref="refs/heads/main",
        environment="pypi",
        workflow_execution_commit="f17254f0fc4bb03b92f581122ea9567d4bf15f12",
        workflow_file_sha256="a" * 64,
        branch_protection_preflight_receipt_sha256="b" * 64,
        observed_branch="main",
        observed_at="2026-08-16T07:33:52Z",
        trusted_owner=publication["trusted_publisher"]["owner"],
        trusted_repository=publication["trusted_publisher"]["repository"],
        trusted_workflow=publication["trusted_publisher"]["workflow"],
        trusted_environment=publication["trusted_publisher"]["environment"],
    )


def test_cli_roundtrip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    kwargs = _base(tmp_path)[2]
    authority_path = tmp_path / "authority.json"
    activation_path = tmp_path / "activation.json"
    args = ["publication-manifests"]
    for key, value in kwargs.items():
        args += [f"--{key.replace('_', '-')}", str(value)]
    args += ["--authority-output", str(authority_path), "--activation-output", str(activation_path)]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True
    authority = json.loads(authority_path.read_text(encoding="utf-8"))
    activation = json.loads(activation_path.read_text(encoding="utf-8"))
    assert authority_path.read_text(encoding="utf-8") == canonical_json(authority)
    assert activation_path.read_text(encoding="utf-8") == canonical_json(activation)
