from __future__ import annotations

import base64
import io
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from tools.release_artifact_policy import ArtifactPolicyError, validate_dist


VERSION = "0.1.1b1"
WHEEL_NAME = f"research_kb_app-{VERSION}-py3-none-any.whl"
SDIST_NAME = f"research_kb_app-{VERSION}.tar.gz"
DIST_INFO = f"research_kb_app-{VERSION}.dist-info"


def _metadata(requirement: str = "research-kb-core[pdf]==0.1.1", *, extra_requirements: tuple[str, ...] = ()) -> bytes:
    return (
        "Metadata-Version: 2.4\n"
        "Name: research-kb-app\n"
        f"Version: {VERSION}\n"
        "License-Expression: Apache-2.0\n"
        f"Requires-Dist: {requirement}\n"
        + "".join(f"Requires-Dist: {item}\n" for item in extra_requirements)
        + "\n"
    ).encode("utf-8")


def _wheel(
    path: Path,
    *,
    requirement: str = "research-kb-core[pdf]==0.1.1",
    extra_requirements: tuple[str, ...] = (),
    frontend: bool = True,
    package: bool = True,
    entry_points: str | None = (
        "[console_scripts]\n"
        "research-kb-app = research_kb_app.entrypoint:main\n"
    ),
) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(f"{DIST_INFO}/METADATA", _metadata(requirement, extra_requirements=extra_requirements))
        archive.writestr(f"{DIST_INFO}/licenses/LICENSE", "Apache License\n")
        archive.writestr("research_kb_app/core-compatibility.json", "{}\n")
        archive.writestr("research_kb_app/web_dist/index.html", "<html></html>\n")
        if frontend:
            archive.writestr("research_kb_app/web_dist/assets/app.js", "export {};\n")
        if package:
            archive.writestr("research_kb_app/__init__.py", "__version__ = '0.1.1b1'\n")
        if entry_points is not None:
            archive.writestr(f"{DIST_INFO}/entry_points.txt", entry_points)


def _sdist(path: Path, *, extra: dict[str, bytes] | None = None, frontend: bool = True) -> None:
    required = {
        "LICENSE": b"Apache License\n",
        "README.md": b"public\n",
        "SECURITY.md": b"security\n",
        "SUPPORT.md": b"support\n",
        "CONTRIBUTING.md": b"contributing\n",
        "CHANGELOG.md": b"changes\n",
        "pyproject.toml": (
            b"[project]\nname = 'research-kb-app'\nversion = '0.1.1b1'\n"
            b"[project.scripts]\n"
            b"research-kb-app = 'research_kb_app.entrypoint:main'\n"
        ),
        "public-source-policy.json": (
            b'{"allow":["README.md"],"contract_version":"research-kb-app-public-source@1",'
            b'"deny":["private/**"],"forbidden_literal_b64":["cHJpdmF0ZQ=="],'
            b'"max_file_bytes":4096,"secret_patterns":["token"]}\n'
        ),
        "docs/architecture.md": b"architecture\n",
        "docs/configuration.md": b"configuration\n",
        "docs/workflow.md": b"workflow\n",
        "docs/r1-operator-guide.md": b"operator\n",
        "src/research_kb_app/__init__.py": b"__version__ = '0.1.1b1'\n",
        "tests/fixtures/p2_small/fixture-manifest.json": b"{}\n",
    }
    if frontend:
        required["web/release/index.html"] = b"<html></html>\n"
        required["web/release/assets/app.js"] = b"export {};\n"
    required.update(extra or {})
    root = f"research_kb_app-{VERSION}"
    with tarfile.open(path, "w:gz") as archive:
        for relative, payload in required.items():
            info = tarfile.TarInfo(f"{root}/{relative}")
            info.size = len(payload)
            archive.addfile(info, io.BytesIO(payload))


def _pair(tmp_path: Path, **wheel_options: object) -> Path:
    _wheel(tmp_path / WHEEL_NAME, **wheel_options)
    _sdist(tmp_path / SDIST_NAME)
    return tmp_path


def test_valid_artifact_pair_is_reported(tmp_path: Path) -> None:
    report = validate_dist(_pair(tmp_path), VERSION)
    assert report["valid"] is True
    assert report["wheel"]["filename"] == WHEEL_NAME
    assert report["sdist"]["filename"] == SDIST_NAME


def test_wrong_core_pin_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ArtifactPolicyError, match="Core Requires-Dist"):
        validate_dist(_pair(tmp_path, requirement="research-kb-core[pdf]==9.9.9"), VERSION)


def test_conflicting_core_requirement_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ArtifactPolicyError, match="exactly one Core Requires-Dist"):
        validate_dist(
            _pair(tmp_path, extra_requirements=("research-kb-core[pdf]==9.9.9",)),
            VERSION,
        )


def test_missing_frontend_asset_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ArtifactPolicyError, match="frontend assets"):
        validate_dist(_pair(tmp_path, frontend=False), VERSION)


def test_missing_python_package_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ArtifactPolicyError, match="required files missing"):
        validate_dist(_pair(tmp_path, package=False), VERSION)


def test_wheel_missing_entry_points_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ArtifactPolicyError, match="entry_points"):
        validate_dist(_pair(tmp_path, entry_points=None), VERSION)


def test_wheel_wrong_console_script_target_is_rejected(tmp_path: Path) -> None:
    wrong = "[console_scripts]\nresearch-kb-app = research_kb_app.launcher:main\n"
    with pytest.raises(ArtifactPolicyError, match="target mismatch"):
        validate_dist(_pair(tmp_path, entry_points=wrong), VERSION)


def test_wheel_extra_console_script_is_rejected(tmp_path: Path) -> None:
    extra = (
        "[console_scripts]\n"
        "research-kb-app = research_kb_app.entrypoint:main\n"
        "unapproved-tool = somewhere.else:main\n"
    )
    with pytest.raises(ArtifactPolicyError, match="exactly one approved console script"):
        validate_dist(_pair(tmp_path, entry_points=extra), VERSION)


def test_wheel_mixed_case_console_script_name_is_rejected(tmp_path: Path) -> None:
    mixed_case = (
        "[console_scripts]\n"
        "Research-KB-App = research_kb_app.entrypoint:main\n"
    )
    with pytest.raises(ArtifactPolicyError, match="exactly one approved console script"):
        validate_dist(_pair(tmp_path, entry_points=mixed_case), VERSION)


def test_wheel_default_inherited_console_script_is_rejected(tmp_path: Path) -> None:
    default_inherited = (
        "[DEFAULT]\n"
        "research-kb-app = research_kb_app.entrypoint:main\n"
        "\n"
        "[console_scripts]\n"
    )
    with pytest.raises(ArtifactPolicyError, match="must not define defaults"):
        validate_dist(_pair(tmp_path, entry_points=default_inherited), VERSION)


def test_sdist_console_script_mapping_is_rejected_when_drifted(tmp_path: Path) -> None:
    _wheel(tmp_path / WHEEL_NAME)
    drifted = (
        b"[project]\nname = 'research-kb-app'\nversion = '0.1.1b1'\n"
        b"[project.scripts]\nresearch-kb-app = 'research_kb_app.launcher:main'\n"
    )
    _sdist(tmp_path / SDIST_NAME, extra={"pyproject.toml": drifted})
    with pytest.raises(ArtifactPolicyError, match="sdist console script mapping"):
        validate_dist(tmp_path, VERSION)


def test_sdist_extra_console_script_is_rejected(tmp_path: Path) -> None:
    _wheel(tmp_path / WHEEL_NAME)
    extra_script = (
        b"[project]\nname = 'research-kb-app'\nversion = '0.1.1b1'\n"
        b"[project.scripts]\n"
        b"research-kb-app = 'research_kb_app.entrypoint:main'\n"
        b"unapproved-tool = 'somewhere.else:main'\n"
    )
    _sdist(tmp_path / SDIST_NAME, extra={"pyproject.toml": extra_script})
    with pytest.raises(ArtifactPolicyError, match="sdist console script mapping"):
        validate_dist(tmp_path, VERSION)


def test_wheel_traversal_is_rejected(tmp_path: Path) -> None:
    _pair(tmp_path)
    with zipfile.ZipFile(tmp_path / WHEEL_NAME, "a") as archive:
        archive.writestr("../escape.txt", "escape\n")
    with pytest.raises(ArtifactPolicyError, match="unsafe archive path"):
        validate_dist(tmp_path, VERSION)


def test_wheel_symlink_is_rejected(tmp_path: Path) -> None:
    _pair(tmp_path)
    link = zipfile.ZipInfo("research_kb_app/link")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(tmp_path / WHEEL_NAME, "a") as archive:
        archive.writestr(link, "target")
    with pytest.raises(ArtifactPolicyError, match="non-regular wheel entry"):
        validate_dist(tmp_path, VERSION)


def test_directory_shaped_symlink_is_rejected(tmp_path: Path) -> None:
    _pair(tmp_path)
    link = zipfile.ZipInfo("research_kb_app/link/")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(tmp_path / WHEEL_NAME, "a") as archive:
        archive.writestr(link, b"")
    with pytest.raises(ArtifactPolicyError, match="non-directory wheel entry"):
        validate_dist(tmp_path, VERSION)


def test_dot_normalized_archive_path_is_rejected(tmp_path: Path) -> None:
    _pair(tmp_path)
    with zipfile.ZipFile(tmp_path / WHEEL_NAME, "a") as archive:
        archive.writestr("./", b"")
    with pytest.raises(ArtifactPolicyError, match="unsafe archive path"):
        validate_dist(tmp_path, VERSION)


def test_sdist_top_level_directory_must_match_version(tmp_path: Path) -> None:
    _wheel(tmp_path / WHEEL_NAME)
    path = tmp_path / SDIST_NAME
    with tarfile.open(path, "w:gz") as archive:
        payload = b"invalid root\n"
        info = tarfile.TarInfo("unexpected-root/README.md")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    with pytest.raises(ArtifactPolicyError, match="top-level directory mismatch"):
        validate_dist(tmp_path, VERSION)


def test_sdist_history_receipt_is_rejected(tmp_path: Path) -> None:
    _wheel(tmp_path / WHEEL_NAME)
    _sdist(tmp_path / SDIST_NAME, extra={"docs/receipts/private.json": b"{}\n"})
    with pytest.raises(ArtifactPolicyError, match="forbidden history"):
        validate_dist(tmp_path, VERSION)


def test_sdist_private_path_is_rejected(tmp_path: Path) -> None:
    _wheel(tmp_path / WHEEL_NAME)
    private_segment = base64.b64decode("cHJpdmF0ZV93b3Jrc3BhY2Vz").decode("ascii")
    _sdist(tmp_path / SDIST_NAME, extra={f"{private_segment}/secret.txt": b"neutral\n"})
    with pytest.raises(ArtifactPolicyError, match="private literal"):
        validate_dist(tmp_path, VERSION)


def test_sdist_frontend_closure_is_required(tmp_path: Path) -> None:
    _wheel(tmp_path / WHEEL_NAME)
    _sdist(tmp_path / SDIST_NAME, frontend=False)
    with pytest.raises(ArtifactPolicyError, match="required files missing"):
        validate_dist(tmp_path, VERSION)


def test_additional_distribution_artifact_is_rejected(tmp_path: Path) -> None:
    _pair(tmp_path)
    (tmp_path / "extra.whl").write_bytes(b"extra")
    with pytest.raises(ArtifactPolicyError, match="only one exact wheel"):
        validate_dist(tmp_path, VERSION)


def test_private_literal_is_rejected_without_embedding_it_in_source(tmp_path: Path) -> None:
    _wheel(tmp_path / WHEEL_NAME)
    private_value = base64.b64decode("QzpcVXNlcnNcMzI1NjNcRG9jdW1lbnRzXENvZGV4")
    _sdist(tmp_path / SDIST_NAME, extra={"src/leak.txt": private_value})
    with pytest.raises(ArtifactPolicyError, match="private literal"):
        validate_dist(tmp_path, VERSION)
