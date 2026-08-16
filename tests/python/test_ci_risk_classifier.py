from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tools.ci_risk_classifier import Change, classify_changes, classify_paths, parse_name_status


def test_docs_only_changes_are_l0() -> None:
    assessment = classify_paths(["README.md", "docs/workflow.md"])

    assert assessment.level == "L0"
    assert assessment.fail_closed is False
    assert assessment.reasons == ("docs_only",)


def test_known_tests_are_low_risk_but_mixed_source_is_higher() -> None:
    tests = classify_paths(["tests/python/test_example.py"])
    mixed = classify_paths(["tests/python/test_example.py", "src/research_kb_app/api.py"])

    assert tests.level == "L1"
    assert mixed.level == "L2"
    assert mixed.fail_closed is False


def test_unknown_path_fails_closed_to_l3() -> None:
    assessment = classify_paths(["new-area/generated-output.bin"])

    assert assessment.level == "L3"
    assert assessment.fail_closed is True
    assert "unknown_path" in assessment.reasons


@pytest.mark.parametrize(
    "path",
    [
        "src/security/auth.py",
        "src/storage/store.py",
        "src/schema/model.py",
        "src/contract/api.py",
        "package.json",
        ".github/workflows/ci.yml",
        "public-source-policy.json",
    ],
)
def test_required_high_risk_categories_are_l3(path: str) -> None:
    assert classify_paths([path]).level == "L3"


@pytest.mark.parametrize(
    "path",
    [
        "tools/ci_risk_classifier.py",
        "tools/release_artifact_policy.py",
        "scripts/verify_release_artifacts.py",
    ],
)
def test_release_governance_paths_are_l3_and_cannot_be_downgraded(path: str) -> None:
    assessment = classify_changes([Change(path=path)], declared_level="L0")

    assert assessment.level == "L3"
    assert assessment.fail_closed is True
    assert "release_governance" in assessment.reasons


def test_high_risk_changes_cannot_be_downgraded() -> None:
    assessment = classify_changes(
        [Change(path=".github/workflows/ci.yml"), Change(path="package-lock.json", status="M")],
        declared_level="L0",
    )

    assert assessment.level == "L3"
    assert {"workflow", "package"}.issubset(assessment.reasons)


def test_rename_delete_and_unknown_status_fail_closed() -> None:
    assessment = classify_changes(
        [
            Change(path="docs/new.md", old_path="docs/old.md", status="R100"),
            Change(path="src/unknown.py", status="???"),
        ]
    )

    assert assessment.level == "L3"
    assert {"rename_or_delete", "unknown_status"}.issubset(assessment.reasons)


@pytest.mark.parametrize(
    "change",
    [
        {"status": "M", "path": "README.md", "old_path": 7},
        ("M", "README.md", 7),
        ("M", "README.md", "old.md", "unexpected"),
    ],
)
def test_malformed_change_fields_fail_closed(change: object) -> None:
    assessment = classify_changes([change])
    assert assessment.level == "L3"
    assert assessment.fail_closed is True


def test_unmerged_status_fails_closed() -> None:
    assessment = classify_changes([Change(path="README.md", status="U")])
    assert assessment.level == "L3"
    assert "unmerged_status" in assessment.reasons


def test_name_status_parser_preserves_rename_information() -> None:
    changes = parse_name_status(["R100\tdocs/old.md\tdocs/new.md", "D\tsrc/old.py"])

    assert changes == (
        Change(path="docs/new.md", status="R100", old_path="docs/old.md"),
        Change(path="src/old.py", status="D"),
    )


def test_cli_emits_json_and_enforces_max_level() -> None:
    script = Path(__file__).resolve().parents[2] / "tools" / "ci_risk_classifier.py"
    result = subprocess.run(
        [sys.executable, str(script), "--json", "--max-level", "L2", "--change", "D:src/old.py"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["level"] == "L3"
    assert payload["fail_closed"] is True


@pytest.mark.parametrize(
    "raw_payload",
    [
        "{}",
        '{"changes": null}',
        '{"changes": "README.md"}',
        '{"changes": {"path": "README.md"}}',
    ],
)
def test_cli_stdin_json_malformed_change_payloads_fail_closed(raw_payload: str) -> None:
    script = Path(__file__).resolve().parents[2] / "tools" / "ci_risk_classifier.py"
    result = subprocess.run(
        [sys.executable, str(script), "--json"],
        input=raw_payload,
        check=False,
        capture_output=True,
        text=True,
    )

    # Exit-code contract unchanged: only --max-level failures return non-zero.
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["level"] == "L3"
    assert payload["fail_closed"] is True


def test_cli_stdin_json_explicit_empty_changes_remains_l0() -> None:
    script = Path(__file__).resolve().parents[2] / "tools" / "ci_risk_classifier.py"
    result = subprocess.run(
        [sys.executable, str(script), "--json"],
        input='{"changes": []}',
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["level"] == "L0"
    assert payload["fail_closed"] is False
