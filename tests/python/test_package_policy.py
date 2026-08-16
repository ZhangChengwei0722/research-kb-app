from __future__ import annotations

import ast
import hashlib
import json
import tomllib
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SUCCESSOR_VERSION = "0.1.1b1"
CORE_PIN = "research-kb-core[pdf]==0.1.1"
EXPECTED_KEYWORDS = {
    "research",
    "knowledge-base",
    "local-first",
    "localhost",
    "pdf",
    "scientific-workflow",
}
EXPECTED_CLASSIFIERS = {
    "Development Status :: 3 - Alpha",
    "License :: OSI Approved :: Apache Software License",
    "Operating System :: Microsoft :: Windows",
    "Programming Language :: Python :: 3",
    "Programming Language :: Python :: 3.11",
    "Programming Language :: Python :: 3.12",
    "Topic :: Scientific/Engineering",
}
GOVERNANCE_FILES = ("LICENSE", "SECURITY.md", "SUPPORT.md", "CONTRIBUTING.md", "CHANGELOG.md")
LICENSE_SHA256 = "c71d239df91726fc519c6eb72d318ec65820627232b2f796219e87dcf35d0ab4"


def _declared_init_version() -> str:
    module = ast.parse(
        (REPO_ROOT / "src" / "research_kb_app" / "__init__.py").read_text(
            encoding="utf-8"
        )
    )
    for node in module.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "__version__":
                    value = ast.literal_eval(node.value)
                    assert isinstance(value, str)
                    return value
    raise AssertionError("__version__ declaration not found")


def test_successor_version_core_pin_and_local_artifact_exclusions() -> None:
    project = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_metadata = project["project"]
    dependencies = project_metadata["dependencies"]
    exclusions = project["tool"]["hatch"]["build"]["exclude"]
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    frontend_package = json.loads(
        (REPO_ROOT / "package.json").read_text(encoding="utf-8")
    )
    frontend_lock = json.loads(
        (REPO_ROOT / "package-lock.json").read_text(encoding="utf-8")
    )

    assert project_metadata["version"] == SUCCESSOR_VERSION
    assert project_metadata["license"] == "Apache-2.0"
    assert project_metadata["license-files"] == ["LICENSE"]
    assert project_metadata["requires-python"] == ">=3.11,<3.13"
    assert EXPECTED_KEYWORDS <= set(project_metadata["keywords"])
    assert EXPECTED_CLASSIFIERS <= set(project_metadata["classifiers"])
    assert "Development Status :: 5 - Production/Stable" not in project_metadata["classifiers"]
    assert "urls" not in project_metadata
    assert "未发布" in readme
    assert "0.1.1b1" in readme
    assert _declared_init_version() == SUCCESSOR_VERSION
    assert frontend_package["private"] is True
    assert frontend_package["version"] == SUCCESSOR_VERSION
    assert frontend_lock["version"] == SUCCESSOR_VERSION
    assert frontend_lock["packages"][""]["version"] == SUCCESSOR_VERSION
    assert CORE_PIN in dependencies
    assert project_metadata["scripts"]["research-kb-app"] == "research_kb_app.entrypoint:main"
    assert ".playwright-cli" in exclusions
    assert ".playwright-cli/**" in exclusions


def test_package_source_parses_with_declared_minimum_python() -> None:
    source_root = REPO_ROOT / "src" / "research_kb_app"
    failures: list[str] = []
    for path in sorted(source_root.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        try:
            ast.parse(
                path.read_text(encoding="utf-8"),
                filename=relative,
                feature_version=(3, 11),
            )
        except SyntaxError as error:
            failures.append(f"{relative}:{error.lineno}:{error.offset}: {error.msg}")

    assert not failures, "Python 3.11 grammar failures:\n" + "\n".join(failures)


def test_public_governance_baseline_is_present_and_pinned() -> None:
    for relative_path in GOVERNANCE_FILES:
        path = REPO_ROOT / relative_path
        assert path.is_file(), f"missing public governance file: {relative_path}"
        assert path.stat().st_size > 0
    assert hashlib.sha256((REPO_ROOT / "LICENSE").read_bytes()).hexdigest() == LICENSE_SHA256
