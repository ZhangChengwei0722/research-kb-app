from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

yaml = pytest.importorskip("yaml")

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_ROOT = REPO_ROOT / ".github" / "workflows"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
EXPECTED_ACTIONS = {
    "actions/checkout": "3d3c42e5aac5ba805825da76410c181273ba90b1",
    "actions/setup-python": "5fda3b95a4ea91299a34e894583c3862153e4b97",
    "actions/setup-node": "820762786026740c76f36085b0efc47a31fe5020",
    "actions/upload-artifact": "043fb46d1a93c77aae656e7c1c64a875d1fc6a0a",
    "actions/download-artifact": "3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c",
}
FORBIDDEN_PUBLICATION_TEXT = re.compile(
    r"(?i)(pypa/gh-action-pypi-publish|npm\s+publish|twine\s+upload|gh\s+release|git\s+tag|repository[_ -]?visibility|visibility\s*[:=])"
)


def _load_yaml(path: Path) -> dict[str, object]:
    # BaseLoader keeps the GitHub Actions key `on` as a string under PyYAML's YAML 1.1 defaults.
    with path.open(encoding="utf-8") as handle:
        value = yaml.load(handle, Loader=yaml.BaseLoader)
    assert isinstance(value, dict)
    return value


def _walk_uses(value: object):
    if isinstance(value, dict):
        for key, item in value.items():
            if key == "uses":
                yield item
            yield from _walk_uses(item)
    elif isinstance(value, list):
        for item in value:
            yield from _walk_uses(item)


def _workflow(name: str) -> dict[str, object]:
    return _load_yaml(WORKFLOW_ROOT / name)


def test_all_workflow_actions_use_accepted_full_shas_and_read_only_permissions() -> None:
    workflow_paths = sorted(WORKFLOW_ROOT.glob("*.y*ml"))
    assert workflow_paths
    for path in workflow_paths:
        workflow = _load_yaml(path)
        assert workflow["permissions"] == {"contents": "read"}
        for use in _walk_uses(workflow):
            assert isinstance(use, str)
            action, separator, ref = use.partition("@")
            assert separator and FULL_SHA.fullmatch(ref), f"unpinned action in {path}: {use}"
            if action in EXPECTED_ACTIONS:
                assert ref == EXPECTED_ACTIONS[action]


def test_ci_is_windows_python_matrix_and_packages_only_after_frontend_artifact() -> None:
    workflow = _workflow("ci.yml")
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    classify = jobs["classify"]
    policy = jobs["policy"]
    frontend = jobs["frontend"]
    python = jobs["python"]
    package = jobs["package"]
    assert isinstance(classify, dict) and classify["outputs"]["level"]
    classify_text = json.dumps(classify, sort_keys=True)
    assert "tools/ci_risk_classifier.py --json" in classify_text
    assert "workflow_dispatch" in classify_text
    assert "level=$level" in classify_text
    assert isinstance(policy, dict) and policy["needs"] == "classify"
    assert "--noconftest" in json.dumps(policy, sort_keys=True)
    assert isinstance(frontend, dict) and frontend["runs-on"] == "windows-latest"
    assert set(frontend["needs"]) == {"classify", "policy"}
    assert "L2" in frontend["if"] and "L3" in frontend["if"]
    assert isinstance(python, dict) and python["runs-on"] == "windows-latest"
    assert set(python["needs"]) == {"classify", "policy"}
    assert "!= 'L0'" in python["if"]
    assert python["strategy"]["matrix"]["python-version"] == ["3.11", "3.12"]
    assert isinstance(package, dict)
    assert set(package["needs"]) == {"classify", "policy", "frontend", "python"}
    assert "needs.classify.outputs.level == 'L3'" in package["if"]
    assert "always()" in package["if"]
    package_text = json.dumps(package, sort_keys=True)
    assert "actions/download-artifact@" in package_text
    assert "frontend-release" in package_text
    assert "scripts/verify_release_artifacts.py" in package_text
    assert package_text.index("python -m build") < package_text.index("scripts/verify_release_artifacts.py")

    frontend_text = json.dumps(frontend, sort_keys=True)
    assert "npm audit --audit-level=high" in frontend_text
    assert frontend_text.index("npm ci") < frontend_text.index("npm audit --audit-level=high")
    assert frontend_text.index("npm audit --audit-level=high") < frontend_text.index("npm test")
    for job in jobs.values():
        assert isinstance(job, dict)
        assert job["runs-on"] == "windows-latest"


def test_ci_risk_routing_keeps_l0_cheap_and_fails_closed_to_l3() -> None:
    workflow = _workflow("ci.yml")
    jobs = workflow["jobs"]
    assert jobs["policy"]["needs"] == "classify"
    assert "L0" not in jobs["frontend"]["if"]
    assert "!= 'L0'" in jobs["python"]["if"]
    assert "== 'L3'" in jobs["package"]["if"]
    classify_text = json.dumps(jobs["classify"], sort_keys=True)
    assert "git diff --name-status --find-renames" in classify_text
    assert "^0+$" in classify_text
    assert classify_text.count("exit 0") >= 2


def test_release_candidate_is_manual_build_only_and_has_no_publication_step() -> None:
    path = WORKFLOW_ROOT / "release-candidate.yml"
    workflow = _workflow(path.name)
    assert set(workflow["on"]) == {"workflow_dispatch"}
    assert "push" not in workflow["on"]
    assert "pull_request" not in workflow["on"]
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)
    assert set(jobs) == {"frontend", "python", "package"}
    assert "frontend" in jobs["package"]["needs"]
    package_text = json.dumps(jobs["package"], sort_keys=True)
    assert "scripts/verify_release_artifacts.py" in package_text
    assert package_text.index("python -m build") < package_text.index("scripts/verify_release_artifacts.py")
    assert package_text.index("scripts/verify_release_artifacts.py") < package_text.index("actions/upload-artifact@")
    frontend_text = json.dumps(jobs["frontend"], sort_keys=True)
    assert "npm audit --audit-level=high" in frontend_text
    assert not FORBIDDEN_PUBLICATION_TEXT.search(path.read_text(encoding="utf-8"))


def test_governance_files_exist_and_package_engine_matches_lock() -> None:
    expected_files = (
        ".github/dependabot.yml",
        ".github/pull_request_template.md",
        ".github/ISSUE_TEMPLATE/config.yml",
        ".github/ISSUE_TEMPLATE/bug_report.yml",
        ".github/ISSUE_TEMPLATE/feature_request.yml",
    )
    for relative in expected_files:
        assert (REPO_ROOT / relative).is_file()

    package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
    lock = json.loads((REPO_ROOT / "package-lock.json").read_text(encoding="utf-8"))
    assert package["engines"] == {"node": ">=24"}
    assert lock["packages"][""]["engines"] == package["engines"]


def test_local_build_script_has_required_order_and_no_publication_command() -> None:
    path = REPO_ROOT / "scripts" / "build_release.ps1"
    text = path.read_text(encoding="utf-8")
    positions = [
        text.index(token)
        for token in (
            "npm ci",
            "npm audit",
            "npm test",
            "npm run typecheck",
            "npm run lint",
            "npm run build",
            "Frontend closure directory",
            "python -m build",
            "--outdir",
            "scripts/verify_release_artifacts.py",
        )
    ]
    assert positions == sorted(positions)
    assert not FORBIDDEN_PUBLICATION_TEXT.search(text)
