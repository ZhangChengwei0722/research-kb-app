"""Deterministic App beta publication identity checks.

This module only creates and verifies operation artifacts; it never performs a
Release or publication operation. Standard library only, so it can run before any
dependency installation on the publication runner.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

AUTHORITY_SCHEMA = "research-kb-app.publication_authority.v1"
ACTIVATION_SCHEMA = "research-kb-app.publication_activation.v1"
MAX_SAFE_INTEGER = (2**53) - 1
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
ID_PATTERN = re.compile(r"^[1-9][0-9]*$")
BETA_VERSION_PATTERN = re.compile(r"^(0\.1\.[0-9]+)(?:b[0-9]+|rc[0-9]+)$")
BETA_TAG_PATTERN = re.compile(r"^v(0\.1\.[0-9]+)(?:b[0-9]+|rc[0-9]+)$")
STABLE_TAG_PATTERN = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
ARTIFACT_NAME = "release-candidate-package"


class GovernanceInputError(ValueError):
    """Raised when an artifact cannot be materialized from valid inputs."""


def canonical_json(value: Any) -> str:
    def validate(item: Any, path: str) -> Any:
        if item is None or isinstance(item, bool):
            return item
        if isinstance(item, int):
            if not -MAX_SAFE_INTEGER <= item <= MAX_SAFE_INTEGER:
                raise TypeError(f"integer outside the safe domain at {path}")
            return item
        if isinstance(item, float):
            raise TypeError(f"floats are not permitted at {path}")
        if isinstance(item, str):
            try:
                item.encode("utf-16-be")
            except UnicodeEncodeError as error:
                raise TypeError(f"unpaired surrogate at {path}") from error
            return item
        if isinstance(item, Mapping):
            result: dict[str, Any] = {}
            for key, value in item.items():
                if not isinstance(key, str):
                    raise TypeError(f"non-string object key at {path}")
                validate(key, f"{path}.<key>")
                result[key] = validate(value, f"{path}.{key!r}")
            return result
        if isinstance(item, (list, tuple)):
            return [validate(value, f"{path}[{index}]") for index, value in enumerate(item)]
        raise TypeError(f"unsupported JSON value at {path}: {type(item).__name__}")

    def escape(value: str) -> str:
        output = ['"']
        for character in value:
            code = ord(character)
            if character == '"':
                output.append('\\"')
            elif character == "\\":
                output.append("\\\\")
            elif code < 0x20:
                output.append(f"\\u{code:04x}")
            else:
                output.append(character)
        output.append('"')
        return "".join(output)

    def render(item: Any) -> str:
        if item is None:
            return "null"
        if isinstance(item, bool):
            return "true" if item else "false"
        if isinstance(item, int):
            return str(item)
        if isinstance(item, str):
            return escape(item)
        if isinstance(item, list):
            return "[" + ",".join(render(value) for value in item) + "]"
        if isinstance(item, Mapping):
            items = sorted(item.items(), key=lambda pair: pair[0].encode("utf-16-be"))
            return "{" + ",".join(escape(key) + ":" + render(value) for key, value in items) + "}"
        raise TypeError("unsupported normalized JSON value")

    return render(validate(value, "$"))


def canonical_json_bytes(value: Any) -> bytes:
    return canonical_json(value).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def _as_id(value: Any) -> str:
    return str(value) if isinstance(value, int) and not isinstance(value, bool) else value


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise GovernanceInputError(message)


def _require_sha256(value: Any, path: str) -> None:
    _require(isinstance(value, str) and SHA256_PATTERN.fullmatch(value), f"{path} must be a lowercase SHA-256 digest")


def _require_commit(value: Any, path: str) -> None:
    _require(isinstance(value, str) and COMMIT_PATTERN.fullmatch(value), f"{path} must be a full commit sha")


def _require_id(value: Any, path: str) -> None:
    normalized = _as_id(value)
    _require(isinstance(normalized, str) and ID_PATTERN.fullmatch(normalized), f"{path} must be a positive decimal id")


def _require_beta(version: str, tag: str) -> None:
    _require(
        isinstance(version, str) and BETA_VERSION_PATTERN.fullmatch(version),
        "App beta version must be a 0.1.x pre-release",
    )
    _require(
        isinstance(tag, str) and BETA_TAG_PATTERN.fullmatch(tag),
        "App beta tag must be a v0.1.x pre-release tag",
    )
    _require(STABLE_TAG_PATTERN.fullmatch(tag) is None, "stable App release tags are forbidden")
    _require(version.startswith("0.1.") and tag == f"v{version}", "tag must match the beta version")


def build_publication_manifests(
    *,
    repository: str,
    source_commit: str,
    workflow_run_id: str | int,
    workflow_run_attempt: str | int,
    version: str,
    artifact_id: str | int,
    artifact_service_digest: str,
    wheel_sha256: str,
    sdist_sha256: str,
    tag: str,
    actor_id: str | int,
    workflow_execution_commit: str,
    workflow_file_sha256: str,
    branch_protection_preflight_receipt_sha256: str,
    observed_branch: str,
    observed_at: str,
    trusted_owner: str,
    trusted_repository: str,
    trusted_workflow: str,
    trusted_environment: str,
    environment: str = "pypi",
) -> tuple[dict[str, Any], dict[str, Any]]:
    _require(repository == f"{trusted_owner}/{trusted_repository}", "repository must match the Trusted Publisher tuple")
    _require_commit(source_commit, "source_commit")
    _require_id(workflow_run_id, "workflow_run_id")
    _require_id(workflow_run_attempt, "workflow_run_attempt")
    _require_beta(version, tag)
    _require_id(artifact_id, "artifact_id")
    _require_sha256(artifact_service_digest, "artifact_service_digest")
    _require_sha256(wheel_sha256, "wheel_sha256")
    _require_sha256(sdist_sha256, "sdist_sha256")
    _require(wheel_sha256 != sdist_sha256, "wheel and sdist digests must differ")
    _require_id(actor_id, "actor_id")
    _require_commit(workflow_execution_commit, "workflow_execution_commit")
    _require_sha256(workflow_file_sha256, "workflow_file_sha256")
    _require_sha256(branch_protection_preflight_receipt_sha256, "preflight receipt")
    _require(observed_branch == "main", "observed branch must be main")
    _require(isinstance(observed_at, str) and observed_at, "observed_at is required")
    _require(environment == "pypi" and trusted_environment == "pypi", "environment must be pypi")
    _require(trusted_workflow == "publish-accepted-release.yml", "unreviewed publisher workflow")
    _require(isinstance(wheel_sha256, str) and wheel_sha256, "wheel digest required")
    _require(isinstance(sdist_sha256, str) and sdist_sha256, "sdist digest required")

    candidate = {
        "repository": repository,
        "source_commit": source_commit,
        "workflow_run_id": _as_id(workflow_run_id),
        "workflow_run_attempt": _as_id(workflow_run_attempt),
        "version": version,
        "artifact_name": ARTIFACT_NAME,
    }
    publication = {
        "accepted_run_id": _as_id(workflow_run_id),
        "accepted_run_attempt": _as_id(workflow_run_attempt),
        "accepted_commit": source_commit,
        "accepted_artifact_name": ARTIFACT_NAME,
        "accepted_artifact_id": _as_id(artifact_id),
        "accepted_artifact_service_digest": artifact_service_digest,
        "accepted_version": version,
        "authorized_actor_id": _as_id(actor_id),
        "tag": tag,
        "workflow_ref": "refs/heads/main",
        "environment": environment,
        "trusted_publisher": {
            "owner": trusted_owner,
            "repository": trusted_repository,
            "workflow": trusted_workflow,
            "environment": trusted_environment,
        },
        "accepted_artifact_digests": {
            f"research_kb_app-{version}-py3-none-any.whl": wheel_sha256,
            f"research_kb_app-{version}.tar.gz": sdist_sha256,
        },
        "workflow_execution_commit": workflow_execution_commit,
        "workflow_file_sha256": workflow_file_sha256,
        "branch_protection_preflight_receipt_sha256": branch_protection_preflight_receipt_sha256,
        "observed_branch": observed_branch,
        "observed_at": observed_at,
    }
    authority = {
        "schema_version": AUTHORITY_SCHEMA,
        "immutable": True,
        "candidate": candidate,
        "publication": publication,
    }
    activation = {
        "schema_version": ACTIVATION_SCHEMA,
        "activation": {
            "enabled": True,
            "publication_authorized": True,
            "build_once": True,
            "rebuild": False,
            "authority_manifest_sha256": canonical_digest(authority),
            **publication,
        },
    }
    return authority, activation


def verify_publication_authority(
    authority: Mapping[str, Any],
    *,
    repository: str,
    actor_id: str,
    accepted_run_id: str,
    accepted_run_attempt: str,
    accepted_commit: str,
    accepted_artifact_id: str,
    accepted_artifact_service_digest: str,
    tag: str,
    workflow_ref: str,
    environment: str,
    workflow_execution_commit: str,
    workflow_file_sha256: str,
    branch_protection_preflight_receipt_sha256: str,
    observed_branch: str,
    observed_at: str,
    trusted_owner: str,
    trusted_repository: str,
    trusted_workflow: str,
    trusted_environment: str,
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(authority, Mapping):
        return False, ["authority must be a JSON object"]
    if set(authority) != {"schema_version", "immutable", "candidate", "publication"}:
        errors.append("authority root fields are not canonical")
    if authority.get("schema_version") != AUTHORITY_SCHEMA:
        errors.append("authority schema is unsupported")
    if authority.get("immutable") is not True:
        errors.append("authority must be immutable")
    candidate = authority.get("candidate") if isinstance(authority.get("candidate"), Mapping) else {}
    publication = authority.get("publication") if isinstance(authority.get("publication"), Mapping) else {}
    try:
        _require_id(candidate.get("workflow_run_id"), "candidate.workflow_run_id")
        _require_id(candidate.get("workflow_run_attempt"), "candidate.workflow_run_attempt")
        _require_commit(candidate.get("source_commit"), "candidate.source_commit")
        _require_beta(str(candidate.get("version", "")), str(publication.get("tag", "")))
        _require_sha256(publication.get("accepted_artifact_service_digest"), "service digest")
        _require_sha256(publication.get("workflow_file_sha256"), "workflow file digest")
        _require_sha256(publication.get("branch_protection_preflight_receipt_sha256"), "preflight")
    except GovernanceInputError as error:
        errors.append(str(error))
    context = {
        "candidate.repository": (candidate.get("repository"), repository),
        "publication.authorized_actor_id": (_as_id(publication.get("authorized_actor_id")), actor_id),
        "publication.accepted_run_id": (_as_id(publication.get("accepted_run_id")), accepted_run_id),
        "publication.accepted_run_attempt": (_as_id(publication.get("accepted_run_attempt")), accepted_run_attempt),
        "publication.accepted_commit": (publication.get("accepted_commit"), accepted_commit),
        "publication.accepted_artifact_id": (_as_id(publication.get("accepted_artifact_id")), accepted_artifact_id),
        "publication.accepted_artifact_service_digest": (publication.get("accepted_artifact_service_digest"), accepted_artifact_service_digest),
        "publication.tag": (publication.get("tag"), tag),
        "publication.workflow_ref": (publication.get("workflow_ref"), workflow_ref),
        "publication.environment": (publication.get("environment"), environment),
        "publication.workflow_execution_commit": (publication.get("workflow_execution_commit"), workflow_execution_commit),
        "publication.workflow_file_sha256": (publication.get("workflow_file_sha256"), workflow_file_sha256),
        "publication.branch_protection_preflight_receipt_sha256": (publication.get("branch_protection_preflight_receipt_sha256"), branch_protection_preflight_receipt_sha256),
        "publication.observed_branch": (publication.get("observed_branch"), observed_branch),
        "publication.observed_at": (publication.get("observed_at"), observed_at),
    }
    for path, (actual, required) in context.items():
        if actual != required:
            errors.append(f"authority context mismatch at {path}")
    trusted = publication.get("trusted_publisher")
    if isinstance(trusted, Mapping):
        for field, value in (
            ("owner", trusted_owner),
            ("repository", trusted_repository),
            ("workflow", trusted_workflow),
            ("environment", trusted_environment),
        ):
            if trusted.get(field) != value:
                errors.append(f"authority Trusted Publisher mismatch at {field}")
    else:
        errors.append("authority Trusted Publisher tuple is missing")
    digests = publication.get("accepted_artifact_digests")
    if not isinstance(digests, Mapping) or len(digests) != 2:
        errors.append("authority must name exactly one wheel and one sdist")
    else:
        for filename, digest in digests.items():
            _require_sha256(digest, filename)
        if not any(str(name).endswith(".whl") for name in digests) or not any(str(name).endswith(".tar.gz") for name in digests):
            errors.append("authority digests must cover one wheel and one sdist")
    return not errors, errors


def build_publication_activation(
    authority: Mapping[str, Any],
    *,
    wheel_path: Path,
    sdist_path: Path,
    downloaded_manifest: Mapping[str, Any],
) -> dict[str, Any]:
    publication = authority.get("publication") if isinstance(authority, Mapping) else {}
    if not isinstance(publication, Mapping):
        raise GovernanceInputError("authority publication section is missing")
    digests = publication.get("accepted_artifact_digests", {})
    expected_wheel = next((name for name in digests if str(name).endswith(".whl")), "")
    expected_sdist = next((name for name in digests if str(name).endswith(".tar.gz")), "")
    _require(wheel_path.name == expected_wheel, "wheel filename differs from authority")
    _require(sdist_path.name == expected_sdist, "sdist filename differs from authority")
    _require(sha256_file(wheel_path) == digests[expected_wheel], "wheel bytes differ from accepted bytes")
    _require(sha256_file(sdist_path) == digests[expected_sdist], "sdist bytes differ from accepted bytes")
    manifest_digests = {}
    if isinstance(downloaded_manifest, Mapping):
        wheel = downloaded_manifest.get("wheel")
        sdist = downloaded_manifest.get("sdist")
        if isinstance(wheel, Mapping) and isinstance(sdist, Mapping):
            manifest_digests = {wheel.get("filename"): wheel.get("sha256"), sdist.get("filename"): sdist.get("sha256")}
    if manifest_digests:
        _require(dict(digests) == manifest_digests, "downloaded manifest digests differ from authority")
    return {
        "schema_version": ACTIVATION_SCHEMA,
        "activation": {
            "enabled": True,
            "publication_authorized": True,
            "build_once": True,
            "rebuild": False,
            "authority_manifest_sha256": canonical_digest(authority),
            **dict(publication),
        },
    }


def _read_json(path: Path) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GovernanceInputError(f"cannot read JSON: {error}") from error
    return value


def _write_json(path: Path, value: Any) -> None:
    # Serialize explicitly; the manifest contains public integrity digests, never
    # credentials, tokens, keys, or passwords.
    with path.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        stream.write("\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="publication_governance")
    commands = parser.add_subparsers(dest="command", required=True)

    manifests = commands.add_parser("publication-manifests")
    for name in (
        "repository", "source_commit", "workflow_run_id", "workflow_run_attempt", "version",
        "artifact_id", "artifact_service_digest", "wheel_sha256", "sdist_sha256", "tag",
        "actor_id", "workflow_execution_commit", "workflow_file_sha256",
        "branch_protection_preflight_receipt_sha256", "observed_branch", "observed_at",
        "trusted_owner", "trusted_repository", "trusted_workflow", "trusted_environment",
        "authority_output", "activation_output",
    ):
        manifests.add_argument(f"--{name.replace('_', '-')}", required=True)

    verify = commands.add_parser("verify-publication-authority")
    verify.add_argument("--manifest", required=True)
    for name in (
        "repository", "actor-id", "accepted-run-id", "accepted-run-attempt", "accepted-commit",
        "accepted-artifact-id", "accepted-artifact-service-digest", "tag", "workflow-ref",
        "environment", "workflow-execution-commit", "workflow-file-sha256",
        "branch-protection-preflight-receipt-sha256", "observed-branch", "observed-at",
        "trusted-owner", "trusted-repository", "trusted-workflow", "trusted-environment",
    ):
        verify.add_argument(f"--{name}", required=True)

    activation = commands.add_parser("publication-activation")
    activation.add_argument("--expected-authority", required=True)
    activation.add_argument("--wheel", required=True)
    activation.add_argument("--sdist", required=True)
    activation.add_argument("--downloaded-manifest", required=True)
    activation.add_argument("--activation-output", required=True)

    verify_activation = commands.add_parser("verify-publication")
    verify_activation.add_argument("--manifest", required=True)
    verify_activation.add_argument("--expected", required=True)
    verify_activation.add_argument("--wheel", required=True)
    verify_activation.add_argument("--sdist", required=True)

    args = parser.parse_args(argv)
    try:
        if args.command == "publication-manifests":
            authority, activation = build_publication_manifests(
                repository=args.repository,
                source_commit=args.source_commit,
                workflow_run_id=args.workflow_run_id,
                workflow_run_attempt=args.workflow_run_attempt,
                version=args.version,
                artifact_id=args.artifact_id,
                artifact_service_digest=args.artifact_service_digest,
                wheel_sha256=args.wheel_sha256,
                sdist_sha256=args.sdist_sha256,
                tag=args.tag,
                actor_id=args.actor_id,
                workflow_execution_commit=args.workflow_execution_commit,
                workflow_file_sha256=args.workflow_file_sha256,
                branch_protection_preflight_receipt_sha256=args.branch_protection_preflight_receipt_sha256,
                observed_branch=args.observed_branch,
                observed_at=args.observed_at,
                trusted_owner=args.trusted_owner,
                trusted_repository=args.trusted_repository,
                trusted_workflow=args.trusted_workflow,
                trusted_environment=args.trusted_environment,
            )
            _write_json(Path(args.authority_output), authority)
            _write_json(Path(args.activation_output), activation)
            print(canonical_json({"ok": True}))
        elif args.command == "verify-publication-authority":
            authority = _read_json(Path(args.manifest))
            ok, errors = verify_publication_authority(
                authority,
                repository=args.repository,
                actor_id=args.actor_id,
                accepted_run_id=args.accepted_run_id,
                accepted_run_attempt=args.accepted_run_attempt,
                accepted_commit=args.accepted_commit,
                accepted_artifact_id=args.accepted_artifact_id,
                accepted_artifact_service_digest=args.accepted_artifact_service_digest,
                tag=args.tag,
                workflow_ref=args.workflow_ref,
                environment=args.environment,
                workflow_execution_commit=args.workflow_execution_commit,
                workflow_file_sha256=args.workflow_file_sha256,
                branch_protection_preflight_receipt_sha256=args.branch_protection_preflight_receipt_sha256,
                observed_branch=args.observed_branch,
                observed_at=args.observed_at,
                trusted_owner=args.trusted_owner,
                trusted_repository=args.trusted_repository,
                trusted_workflow=args.trusted_workflow,
                trusted_environment=args.trusted_environment,
            )
            print(canonical_json({"ok": ok, "errors": errors}))
            return 0 if ok else 1
        elif args.command == "publication-activation":
            authority = _read_json(Path(args.expected_authority))
            downloaded = _read_json(Path(args.downloaded_manifest))
            value = build_publication_activation(
                authority,
                wheel_path=Path(args.wheel),
                sdist_path=Path(args.sdist),
                downloaded_manifest=downloaded,
            )
            _write_json(Path(args.activation_output), value)
            print(canonical_json({"ok": True}))
        else:
            activation = _read_json(Path(args.manifest))
            authority = _read_json(Path(args.expected))
            ok, errors = _verify_activation(activation, authority, Path(args.wheel), Path(args.sdist))
            print(canonical_json({"ok": ok, "errors": errors}))
            return 0 if ok else 1
    except (OSError, GovernanceInputError, TypeError, ValueError, json.JSONDecodeError) as error:
        print(canonical_json({"ok": False, "errors": [str(error)]}))
        return 1
    return 0


def _verify_activation(
    activation: Mapping[str, Any],
    authority: Mapping[str, Any],
    wheel: Path,
    sdist: Path,
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    if not isinstance(activation, Mapping) or activation.get("schema_version") != ACTIVATION_SCHEMA:
        return False, ["activation schema is unsupported"]
    body = activation.get("activation")
    if not isinstance(body, Mapping):
        return False, ["activation body is missing"]
    if body.get("enabled") is not True or body.get("publication_authorized") is not True:
        errors.append("activation is not authorized")
    if body.get("build_once") is not True or body.get("rebuild") is not False:
        errors.append("activation must not rebuild")
    if body.get("authority_manifest_sha256") != canonical_digest(authority):
        errors.append("activation authority digest mismatch")
    publication = authority.get("publication") if isinstance(authority.get("publication"), Mapping) else {}
    for field in ("accepted_artifact_digests", "accepted_artifact_service_digest", "accepted_artifact_id", "tag", "workflow_ref", "environment"):
        if body.get(field) != publication.get(field):
            errors.append(f"activation field mismatch: {field}")
    try:
        build_publication_activation(authority, wheel_path=wheel, sdist_path=sdist, downloaded_manifest={})
    except GovernanceInputError as error:
        errors.append(str(error))
    return not errors, errors


if __name__ == "__main__":
    raise SystemExit(main())
