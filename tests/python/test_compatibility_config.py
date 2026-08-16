from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
from dataclasses import replace
from importlib import metadata
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from research_kb_app import config as config_module
from research_kb_app.compatibility import (
    CAPABILITY_PROFILE_ALGORITHM,
    CompatibilityError,
    build_compatibility_marker,
    capability_profile_facts,
    dependency_closure_names,
    dependency_profile_facts,
    load_compatibility,
    requires_dist_facts,
    runtime_selector_facts,
    runtime_payload_facts,
    verify_core_wheel,
    verify_installed_core,
)
from research_kb_app.config import AppConfigError, load_app_config


def test_installed_core_matches_frozen_contract() -> None:
    compatibility = load_compatibility()
    assert verify_installed_core(compatibility) == {
        "package_version": "0.1.1",
        "application_service_interface_version": "1.23",
        "catalog_contract_version": "1.0",
    }


def test_installed_core_mismatch_fails_closed() -> None:
    compatibility = replace(load_compatibility(), package_version="9.9.9")
    with pytest.raises(CompatibilityError, match="incompatible"):
        verify_installed_core(compatibility)


def test_dependency_failure_precedes_capability_inspection(monkeypatch) -> None:
    capability_inspected = False

    def fail_dependency_profile(*_args, **_kwargs):
        raise CompatibilityError("dependency profile rejected before capability inspection")

    def inspect_capabilities(_self):
        nonlocal capability_inspected
        capability_inspected = True
        return {}

    monkeypatch.setattr(
        "research_kb_app.compatibility.dependency_profile_facts",
        fail_dependency_profile,
    )
    monkeypatch.setattr(
        "research_kb_app.compatibility.CapabilityService.show",
        inspect_capabilities,
    )

    with pytest.raises(CompatibilityError, match="before capability inspection"):
        verify_installed_core(load_compatibility())

    assert capability_inspected is False


@pytest.mark.parametrize(
    ("service_name", "loader_name"),
    [
        ("CapabilityService", "_load_capability_report"),
        ("CatalogCapabilityService", "_load_catalog_report"),
    ],
)
def test_core_report_loaders_normalize_service_raises(
    monkeypatch: pytest.MonkeyPatch, service_name: str, loader_name: str
) -> None:
    from research_kb_app import compatibility

    service = getattr(compatibility, service_name)

    def crash(_self):
        raise RuntimeError("synthetic service crash")

    monkeypatch.setattr(service, "show", crash)
    with pytest.raises(compatibility.CompatibilityError, match="report is unavailable"):
        getattr(compatibility, loader_name)()


@pytest.mark.parametrize(
    ("service_name", "loader_name"),
    [
        ("CapabilityService", "_load_capability_report"),
        ("CatalogCapabilityService", "_load_catalog_report"),
    ],
)
def test_core_report_loaders_reject_non_mapping_results(
    monkeypatch: pytest.MonkeyPatch, service_name: str, loader_name: str
) -> None:
    from research_kb_app import compatibility

    service = getattr(compatibility, service_name)

    def malformed(_self):
        return ["not", "a", "mapping"]

    monkeypatch.setattr(service, "show", malformed)
    with pytest.raises(compatibility.CompatibilityError, match="report is invalid"):
        getattr(compatibility, loader_name)()


def test_installed_core_missing_evidence_source_capability_fails_closed(monkeypatch) -> None:
    monkeypatch.setattr(
        "research_kb_app.compatibility.CapabilityService.show",
        lambda _self: {"features": {"reading_evidence_source_access": False}},
    )
    with pytest.raises(CompatibilityError, match="Evidence source access"):
        verify_installed_core(load_compatibility())


def test_installed_core_missing_tag_application_fails_closed(monkeypatch) -> None:
    capabilities = {
        "features": {
            "reading_evidence_source_access": True,
            "knowledge_query_agent_tasks": True,
            "discovery_application_service": True,
            "tag_application": False,
        }
    }
    monkeypatch.setattr(
        "research_kb_app.compatibility.CapabilityService.show",
        lambda _self: capabilities,
    )
    with pytest.raises(CompatibilityError, match="Tag Application Service"):
        verify_installed_core(load_compatibility())


@pytest.mark.parametrize(
    ("query_filters", "message"),
    [
        ("tag_id", "catalog report is invalid"),                 # str is not a list
        ([7, "tag_id"], "catalog report is invalid"),            # non-str item
        (["", "tag_id"], "catalog report is invalid"),           # empty item
        (["paper_id", "question_id"], "Tag Catalog filter"),     # valid list, missing tag_id
    ],
)
def test_installed_core_catalog_query_filters_shape_fails_closed(
    monkeypatch, query_filters: object, message: str
) -> None:
    monkeypatch.setattr(
        "research_kb_app.compatibility.CatalogCapabilityService.show",
        lambda _self, value=query_filters: {"query_filters": value},
    )
    with pytest.raises(CompatibilityError, match=message):
        verify_installed_core(load_compatibility())


def test_installed_core_missing_question_screening_tasks_fails_closed(monkeypatch) -> None:
    capabilities = {
        "features": {
            "reading_evidence_source_access": True,
            "knowledge_query_agent_tasks": True,
            "discovery_application_service": True,
            "tag_application": True,
            "question_screening_agent_tasks": False,
            "research_synthesis_application": True,
            "research_synthesis_agent_tasks": True,
        }
    }
    monkeypatch.setattr(
        "research_kb_app.compatibility.CapabilityService.show",
        lambda _self: capabilities,
    )
    with pytest.raises(CompatibilityError, match="Question Screening Agent Tasks"):
        verify_installed_core(load_compatibility())


@pytest.mark.parametrize(
    ("missing_feature", "message"),
    [
        ("research_synthesis_application", "Research Synthesis Application Service"),
        ("research_synthesis_agent_tasks", "Research Synthesis Agent Tasks"),
    ],
)
def test_installed_core_missing_research_synthesis_capability_fails_closed(
    monkeypatch,
    missing_feature: str,
    message: str,
) -> None:
    features = {
        "reading_evidence_source_access": True,
        "knowledge_query_agent_tasks": True,
        "discovery_application_service": True,
        "tag_application": True,
        "question_screening_agent_tasks": True,
        "research_synthesis_application": True,
        "research_synthesis_agent_tasks": True,
    }
    features[missing_feature] = False
    monkeypatch.setattr(
        "research_kb_app.compatibility.CapabilityService.show",
        lambda _self: {"features": features},
    )
    with pytest.raises(CompatibilityError, match=message):
        verify_installed_core(load_compatibility())


def test_installed_core_missing_obsidian_generated_views_fails_closed(monkeypatch) -> None:
    features = {
        "reading_evidence_source_access": True,
        "knowledge_query_agent_tasks": True,
        "discovery_application_service": True,
        "tag_application": True,
        "question_screening_agent_tasks": True,
        "research_synthesis_application": True,
        "research_synthesis_agent_tasks": True,
        "obsidian_generated_views": False,
    }
    monkeypatch.setattr(
        "research_kb_app.compatibility.CapabilityService.show",
        lambda _self: {"features": features},
    )
    with pytest.raises(CompatibilityError, match="Obsidian Generated Views"):
        verify_installed_core(load_compatibility())


def test_installed_core_missing_exchange_capability_fails_closed(monkeypatch) -> None:
    features = {
        "reading_evidence_source_access": True,
        "knowledge_query_agent_tasks": True,
        "discovery_application_service": True,
        "tag_application": True,
        "question_screening_agent_tasks": True,
        "research_synthesis_application": True,
        "research_synthesis_agent_tasks": True,
        "obsidian_generated_views": True,
        "exchange_source_free_export": True,
        "exchange_source_inclusive_export": True,
        "exchange_import": False,
    }
    monkeypatch.setattr(
        "research_kb_app.compatibility.CapabilityService.show",
        lambda _self: {"features": features},
    )
    with pytest.raises(CompatibilityError, match="Exchange import"):
        verify_installed_core(load_compatibility())


def test_core_wheel_verification_fails_closed(tmp_path: Path) -> None:
    wheel = tmp_path / "core.whl"
    wheel.write_bytes(b"not the reviewed wheel")
    with pytest.raises(CompatibilityError, match="digest"):
        verify_core_wheel(wheel, load_compatibility())


class _FakeDistribution:
    def __init__(
        self,
        root: Path,
        *,
        files: list[str] | None = None,
        metadata_text: str = "Metadata-Version: 2.4\nName: research-kb-core\n",
    ) -> None:
        self.root = root
        self.files = [PurePosixPath(value) for value in (files or [])]
        self.metadata = {"Name": "research-kb-core"}
        self.version = "0.1.1"
        self._metadata_text = metadata_text

    def locate_file(self, value) -> Path:
        return self.root.joinpath(*PurePosixPath(str(value)).parts)

    def read_text(self, name: str) -> str | None:
        return self._metadata_text if name == "METADATA" else None


def test_runtime_payload_uses_distribution_environment_and_detects_unrecorded_package_file(
    tmp_path: Path,
) -> None:
    package = tmp_path / "research_kb"
    package.mkdir()
    (package / "__init__.py").write_text("VERSION = 1\n", encoding="utf-8")
    (package / "unexpected.py").write_text("UNEXPECTED = True\n", encoding="utf-8")
    distribution = _FakeDistribution(tmp_path, files=["research_kb/__init__.py"])

    facts = runtime_payload_facts(distribution)

    assert facts["file_count"] == 2


def test_requires_dist_preserves_duplicates_and_spacing_changes_digest(tmp_path: Path) -> None:
    duplicate = _FakeDistribution(
        tmp_path,
        metadata_text=(
            "Metadata-Version: 2.4\n"
            "Name: research-kb-core\n"
            "Requires-Dist: alpha>=1\n"
            "Requires-Dist: alpha>=1\n"
        ),
    )
    spaced = _FakeDistribution(
        tmp_path,
        metadata_text=(
            "Metadata-Version: 2.4\n"
            "Name: research-kb-core\n"
            "Requires-Dist: alpha >=1\n"
            "Requires-Dist: alpha>=1\n"
        ),
    )

    duplicate_facts = requires_dist_facts(duplicate)
    spaced_facts = requires_dist_facts(spaced)

    assert duplicate_facts["values"] == ["alpha>=1", "alpha>=1"]
    assert spaced_facts["values"] == ["alpha >=1", "alpha>=1"]
    assert duplicate_facts["sha256"] != spaced_facts["sha256"]


def test_distribution_path_escape_fails_closed(tmp_path: Path, monkeypatch) -> None:
    from research_kb_app import compatibility as compatibility_module

    distribution = _FakeDistribution(tmp_path, files=["../outside.py"])
    (tmp_path.parent / "outside.py").write_text("unsafe\n", encoding="utf-8")

    with pytest.raises(CompatibilityError, match="unsafe path"):
        compatibility_module._distribution_files(distribution)


def test_distribution_missing_file_fails_closed(tmp_path: Path) -> None:
    from research_kb_app import compatibility as compatibility_module

    distribution = _FakeDistribution(tmp_path, files=["alpha/missing.py"])

    with pytest.raises(CompatibilityError, match="file is missing"):
        compatibility_module._distribution_files(distribution)


EXPECTED_CORE_PDF_DEPENDENCY_CLOSURE = [
    "attrs",
    "cffi",
    "charset-normalizer",
    "cryptography",
    "filelock",
    "jsonschema",
    "jsonschema-specifications",
    "pdfminer-six",
    "pdfplumber",
    "pillow",
    "pycparser",
    "pypdfium2",
    "pyyaml",
    "referencing",
    "rpds-py",
    "typing-extensions",
]


def test_core_pdf_dependency_closure_is_derived_from_installed_metadata() -> None:
    assert dependency_closure_names("research-kb-core", extras=("pdf",)) == tuple(
        EXPECTED_CORE_PDF_DEPENDENCY_CLOSURE
    )


@pytest.mark.parametrize(
    "roster",
    [
        EXPECTED_CORE_PDF_DEPENDENCY_CLOSURE[:-1],
        [*EXPECTED_CORE_PDF_DEPENDENCY_CLOSURE, "packaging"],
    ],
)
def test_dependency_profile_rejects_inexact_closure_roster(roster: list[str]) -> None:
    requires_dist = requires_dist_facts(metadata.distribution("research-kb-core"))

    with pytest.raises(CompatibilityError, match="does not match the required closure"):
        dependency_profile_facts(roster, requires_dist=requires_dist)


def test_marker_contains_closed_python311_and_python312_runtime_profiles() -> None:
    marker = json.loads(
        (Path(__file__).resolve().parents[2] / "core-compatibility.json").read_text(
            encoding="utf-8"
        )
    )

    assert marker["compatibility_schema_version"] == "research-kb-app-core-compatibility@3"
    assert [item["runtime_selector"] for item in marker["dependency_profiles"]] == [
        {
            "abi_tag": "cp311",
            "implementation": "cpython",
            "interpreter_tag": "cp311",
            "platform_tag": "win_amd64",
            "python_minor": "3.11",
        },
        {
            "abi_tag": "cp312",
            "implementation": "cpython",
            "interpreter_tag": "cp312",
            "platform_tag": "win_amd64",
            "python_minor": "3.12",
        },
    ]
    assert {item["profile_id"] for item in marker["dependency_profiles"]} == {
        "core_pdf_beta_v1"
    }
    python311, python312 = marker["dependency_profiles"]
    identity311 = {
        item["name"]: (item["version"], item["file_count"])
        for item in python311["distributions"]
    }
    identity312 = {
        item["name"]: (item["version"], item["file_count"])
        for item in python312["distributions"]
    }
    payload311 = {
        item["name"]: item["installed_payload_sha256"]
        for item in python311["distributions"]
    }
    payload312 = {
        item["name"]: item["installed_payload_sha256"]
        for item in python312["distributions"]
    }
    assert identity311 == identity312
    assert {
        name for name in payload311 if payload311[name] != payload312[name]
    } == {"cffi", "charset-normalizer", "pillow", "pyyaml", "rpds-py"}


def test_runtime_selector_uses_current_minor_and_primary_packaging_tag() -> None:
    from packaging.tags import sys_tags
    from research_kb_app import compatibility as compatibility_module

    primary = next(sys_tags())

    assert compatibility_module.runtime_selector_facts() == {
        "abi_tag": primary.abi,
        "implementation": sys.implementation.name,
        "interpreter_tag": primary.interpreter,
        "platform_tag": primary.platform,
        "python_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def test_marker_builder_combines_current_and_reviewed_other_runtime_profile() -> None:
    compatibility = load_compatibility()
    current_selector = runtime_selector_facts()
    other_profiles = [
        item
        for item in compatibility.dependency_profiles
        if item["runtime_selector"] != current_selector
    ]

    marker = build_compatibility_marker(
        core_commit=compatibility.core_commit,
        wheel_sha256=compatibility.wheel_sha256,
        dependency_distribution_names=[
            item["name"]
            for item in compatibility.dependency_profiles[0]["distributions"]
        ],
        additional_dependency_profiles=other_profiles,
    )

    assert marker == json.loads(
        (Path(__file__).resolve().parents[2] / "core-compatibility.json").read_text(
            encoding="utf-8"
        )
    )


def test_marker_generation_cli_rebuilds_checked_in_marker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tools.generate_compatibility_marker import main

    compatibility = load_compatibility()
    peer_profile = next(
        item
        for item in compatibility.dependency_profiles
        if item["runtime_selector"] != runtime_selector_facts()
    )
    peer_path = tmp_path / "reviewed-peer-profile.json"
    peer_path.write_text(json.dumps(peer_profile), encoding="utf-8")
    output_path = tmp_path / "generated-marker.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_compatibility_marker.py",
            "--core-commit",
            compatibility.core_commit,
            "--wheel-sha256",
            compatibility.wheel_sha256,
            "--output",
            str(output_path),
            "--reviewed-runtime-profile",
            str(peer_path),
            *[
                value
                for item in compatibility.dependency_profiles[0]["distributions"]
                for value in ("--dependency-name", item["name"])
            ],
        ],
    )

    assert main() == 0
    assert json.loads(output_path.read_text(encoding="utf-8")) == json.loads(
        (Path(__file__).resolve().parents[2] / "core-compatibility.json").read_text(
            encoding="utf-8"
        )
    )


@pytest.mark.parametrize("peer_count", [0, 2])
def test_marker_generation_cli_requires_exactly_one_reviewed_peer_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    peer_count: int,
) -> None:
    from tools.generate_compatibility_marker import main

    compatibility = load_compatibility()
    peer_profile = next(
        item
        for item in compatibility.dependency_profiles
        if item["runtime_selector"] != runtime_selector_facts()
    )
    peer_path = tmp_path / "reviewed-peer-profile.json"
    peer_path.write_text(json.dumps(peer_profile), encoding="utf-8")
    output_path = tmp_path / "generated-marker.json"
    peer_args = [
        value
        for _ in range(peer_count)
        for value in ("--reviewed-runtime-profile", str(peer_path))
    ]
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_compatibility_marker.py",
            "--core-commit",
            compatibility.core_commit,
            "--wheel-sha256",
            compatibility.wheel_sha256,
            "--output",
            str(output_path),
            "--dependency-name",
            compatibility.dependency_profiles[0]["distributions"][0]["name"],
            *peer_args,
        ],
    )

    with pytest.raises(SystemExit):
        main()
    assert not output_path.exists()


def test_marker_generation_cli_rejects_invalid_reviewed_peer_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tools.generate_compatibility_marker import main

    compatibility = load_compatibility()
    peer_profile = copy.deepcopy(
        next(
            item
            for item in compatibility.dependency_profiles
            if item["runtime_selector"] != runtime_selector_facts()
        )
    )
    peer_profile["unexpected"] = True
    peer_path = tmp_path / "invalid-reviewed-peer-profile.json"
    peer_path.write_text(json.dumps(peer_profile), encoding="utf-8")
    output_path = tmp_path / "generated-marker.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "generate_compatibility_marker.py",
            "--core-commit",
            compatibility.core_commit,
            "--wheel-sha256",
            compatibility.wheel_sha256,
            "--output",
            str(output_path),
            "--reviewed-runtime-profile",
            str(peer_path),
            *[
                value
                for item in compatibility.dependency_profiles[0]["distributions"]
                for value in ("--dependency-name", item["name"])
            ],
        ],
    )

    with pytest.raises(SystemExit):
        main()
    assert "runtime profile has an invalid shape" in capsys.readouterr().err
    assert not output_path.exists()


@pytest.mark.parametrize(
    "mutation",
    [
        "duplicate_selector",
        "missing_profile",
        "noncanonical_order",
        "third_profile",
        "version_drift",
        "duplicate_payload",
        "file_count_drift",
    ],
)
def test_dependency_runtime_profile_set_shape_fails_closed(
    tmp_path: Path, mutation: str
) -> None:
    marker = json.loads(
        (Path(__file__).resolve().parents[2] / "core-compatibility.json").read_text(
            encoding="utf-8"
        )
    )
    profiles = marker["dependency_profiles"]
    if mutation == "duplicate_selector":
        duplicate = copy.deepcopy(profiles[0])
        duplicate["installed_payload_sha256"] = "0" * 64
        profiles.append(duplicate)
    elif mutation == "missing_profile":
        profiles.pop()
    elif mutation == "noncanonical_order":
        profiles.reverse()
    elif mutation == "third_profile":
        third = copy.deepcopy(profiles[-1])
        third["runtime_selector"] = {
            "abi_tag": "cp313",
            "implementation": "cpython",
            "interpreter_tag": "cp313",
            "platform_tag": "win_amd64",
            "python_minor": "3.13",
        }
        third["installed_payload_sha256"] = "0" * 64
        profiles.append(third)
    elif mutation == "version_drift":
        profiles[1]["distributions"][0]["version"] = "0.0"
    elif mutation == "duplicate_payload":
        profiles[1]["installed_payload_sha256"] = profiles[0]["installed_payload_sha256"]
    else:
        profiles[1]["distributions"][0]["file_count"] += 1
        profiles[1]["installed_payload_file_count"] += 1
    path = tmp_path / "marker.json"
    path.write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(CompatibilityError):
        load_compatibility(path)


def test_dependency_payload_member_drift_fails_before_capability_inspection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from research_kb_app import compatibility as compatibility_module

    marker = json.loads(
        (Path(__file__).resolve().parents[2] / "core-compatibility.json").read_text(
            encoding="utf-8"
        )
    )
    current_selector = runtime_selector_facts()
    current_profile = next(
        item
        for item in marker["dependency_profiles"]
        if item["runtime_selector"] == current_selector
    )
    current_profile["distributions"][0]["installed_payload_sha256"] = "0" * 64
    frozen_lock = [
        {
            "installed_payload_sha256": item["installed_payload_sha256"],
            "name": item["name"],
            "version": item["version"],
        }
        for item in current_profile["distributions"]
    ]
    current_profile["frozen_lock_sha256"] = hashlib.sha256(
        json.dumps(
            frozen_lock,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    path = tmp_path / "marker.json"
    path.write_text(json.dumps(marker), encoding="utf-8")
    capability_inspected = False

    def inspect_capabilities(_self):
        nonlocal capability_inspected
        capability_inspected = True
        return {}

    monkeypatch.setattr(
        compatibility_module.CapabilityService,
        "show",
        inspect_capabilities,
    )

    with pytest.raises(CompatibilityError, match="dependency profile is stale"):
        verify_installed_core(load_compatibility(path))
    assert capability_inspected is False


def test_unknown_runtime_selector_fails_before_capability_inspection(monkeypatch) -> None:
    from research_kb_app import compatibility as compatibility_module

    capability_inspected = False

    def inspect_capabilities(_self):
        nonlocal capability_inspected
        capability_inspected = True
        return {}

    monkeypatch.setattr(
        compatibility_module,
        "runtime_selector_facts",
        lambda: {
            "abi_tag": "cp313",
            "implementation": "cpython",
            "interpreter_tag": "cp313",
            "platform_tag": "win_amd64",
            "python_minor": "3.13",
        },
    )
    monkeypatch.setattr(
        compatibility_module.CapabilityService,
        "show",
        inspect_capabilities,
    )

    with pytest.raises(CompatibilityError, match="runtime dependency profile"):
        verify_installed_core(load_compatibility())

    assert capability_inspected is False


def test_runtime_selector_cannot_be_rebound_to_other_frozen_payload(
    tmp_path: Path, monkeypatch
) -> None:
    from research_kb_app import compatibility as compatibility_module

    marker = json.loads(
        (Path(__file__).resolve().parents[2] / "core-compatibility.json").read_text(
            encoding="utf-8"
        )
    )
    python311, python312 = marker["dependency_profiles"]
    selector311 = python311["runtime_selector"]
    selector312 = python312["runtime_selector"]
    marker["dependency_profiles"] = [
        {**python312, "runtime_selector": selector311},
        {**python311, "runtime_selector": selector312},
    ]
    path = tmp_path / "marker.json"
    path.write_text(json.dumps(marker), encoding="utf-8")
    capability_inspected = False

    def inspect_capabilities(_self):
        nonlocal capability_inspected
        capability_inspected = True
        return {}

    monkeypatch.setattr(
        compatibility_module.CapabilityService,
        "show",
        inspect_capabilities,
    )

    with pytest.raises(CompatibilityError, match="dependency profile is stale"):
        verify_installed_core(load_compatibility(path))

    assert capability_inspected is False


def test_duplicate_installed_distribution_identity_fails_closed(monkeypatch) -> None:
    from research_kb_app import compatibility as compatibility_module

    duplicate = metadata.distribution("research-kb-core")
    monkeypatch.setattr(
        compatibility_module,
        "_find_distributions",
        lambda _name: [duplicate, duplicate],
    )

    with pytest.raises(CompatibilityError, match="installed more than once"):
        dependency_closure_names("research-kb-core", extras=("pdf",))


def test_unselected_capability_facts_do_not_change_selected_profile() -> None:
    from research_kb.services import CapabilityService

    report = CapabilityService().show()
    baseline = capability_profile_facts(
        report,
        "core_pdf_beta_v1",
        expected_core_version="0.1.1",
        expected_pdf_version="0.11.10",
    )
    changed = json.loads(json.dumps(report))
    changed["features"]["future_unselected_capability"] = True

    assert capability_profile_facts(
        changed,
        "core_pdf_beta_v1",
        expected_core_version="0.1.1",
        expected_pdf_version="0.11.10",
    ) == baseline


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("availability", "unavailable"),
        ("diagnostic_code", "dependency_missing"),
        ("version", "0.11.9"),
    ],
)
def test_required_capability_adapter_mismatch_fails_closed(field: str, value: str) -> None:
    from research_kb.services import CapabilityService

    report = json.loads(json.dumps(CapabilityService().show()))
    report["parse_adapters"][0][field] = value

    with pytest.raises(CompatibilityError, match="adapter is unavailable or incompatible"):
        capability_profile_facts(
            report,
            "core_pdf_beta_v1",
            expected_core_version="0.1.1",
            expected_pdf_version="0.11.10",
        )


@pytest.mark.parametrize(
    "feature",
    [
        "trusted_parse_authority",
        "supervised_pdf_parse",
        "trusted_parse_intake_application",
        "intake_source_adequacy_resolution",
    ],
)
def test_required_capability_profile_feature_mismatch_fails_closed(feature: str) -> None:
    from research_kb.services import CapabilityService

    report = json.loads(json.dumps(CapabilityService().show()))
    report["features"][feature] = False

    with pytest.raises(CompatibilityError, match="capability identity is invalid"):
        capability_profile_facts(
            report,
            "core_pdf_beta_v1",
            expected_core_version="0.1.1",
            expected_pdf_version="0.11.10",
        )


@pytest.mark.parametrize(
    "mutation",
    ["profile_id", "interface_version", "adapter_version", "dependency_file_count"],
)
def test_marker_cross_field_inconsistency_fails_closed(
    tmp_path: Path, mutation: str
) -> None:
    marker = json.loads((Path(__file__).resolve().parents[2] / "core-compatibility.json").read_text(encoding="utf-8"))
    if mutation == "profile_id":
        marker["dependency_profiles"][0]["profile_id"] = "other-profile"
        marker["capability_profile"]["selected_facts"]["required_dependency_profile_id"] = "other-profile"
    elif mutation == "interface_version":
        marker["capability_profile"]["selected_facts"]["interface_version"] = "9.9"
    elif mutation == "dependency_file_count":
        marker["dependency_profiles"][0]["installed_payload_file_count"] += 1
    else:
        for adapter in marker["capability_profile"]["selected_facts"]["parse_adapters"]:
            adapter["version"] = "9.9"
    selected = marker["capability_profile"]["selected_facts"]
    marker["capability_profile"]["algorithm"] = CAPABILITY_PROFILE_ALGORITHM
    marker["capability_profile"]["sha256"] = hashlib.sha256(
        json.dumps(selected, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("utf-8")
    ).hexdigest()
    path = tmp_path / "marker.json"
    path.write_text(json.dumps(marker), encoding="utf-8")

    with pytest.raises(CompatibilityError):
        load_compatibility(path)


def test_config_rejects_unknown_keys_and_relative_roots(tmp_path: Path) -> None:
    payload = {
        "contract_version": "research-kb-app-config@1.0",
        "workspaces": [],
        "state_root": "relative",
        "log_root": "relative/logs",
        "frontend_root": "relative/frontend",
        "request_budgets": {},
        "unexpected": True,
    }
    path = tmp_path / "config.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(AppConfigError, match="unknown or missing"):
        load_app_config(path)


def _valid_app_config_payload(tmp_path: Path) -> dict[str, object]:
    workspace_root = tmp_path / "workspaces" / "p2-small"
    workspace_root.mkdir(parents=True)
    workspace_config = workspace_root / "workspace.yaml"
    workspace_config.write_text("workspace: {}\n", encoding="utf-8")
    frontend_root = tmp_path / "frontend"
    frontend_root.mkdir()
    (frontend_root / "index.html").write_text("<!doctype html>\n", encoding="utf-8")
    vault_root = tmp_path / "vault"
    vault_root.mkdir()
    state_root = tmp_path / "state"
    return {
        "contract_version": "research-kb-app-config@1.1",
        "workspaces": [
            {
                "option_id": "p2-small",
                "label": "P2 Small Synthetic",
                "config_path": str(workspace_config.resolve()),
            }
        ],
        "state_root": str(state_root.resolve()),
        "log_root": str((state_root / "logs").resolve()),
        "frontend_root": str(frontend_root.resolve()),
        "request_budgets": {
            "max_body_bytes": 4096,
            "max_query_bytes": 1024,
            "max_page_size": 100,
            "request_timeout_seconds": 30,
        },
        "obsidian_targets": [
            {
                "target_id": "primary-vault",
                "label": "Primary Vault",
                "workspace_option_id": "p2-small",
                "vault_root": str(vault_root.resolve()),
                "managed_subtree": "Research KB",
                "personal_notes_subtree": "Personal",
            }
        ],
    }


def _trap_remote_filesystem_touch(monkeypatch: pytest.MonkeyPatch) -> None:
    original_resolve = Path.resolve
    original_is_file = Path.is_file
    original_is_dir = Path.is_dir
    original_read_text = Path.read_text
    original_lexists = os.path.lexists
    original_lstat = os.lstat

    def remote(value: object) -> bool:
        return str(value).startswith("\\\\")

    def resolve(path: Path, *, strict: bool = False) -> Path:
        if remote(path):
            raise AssertionError("lexical UNC path reached Path.resolve")
        return original_resolve(path, strict=strict)

    def is_file(path: Path) -> bool:
        if remote(path):
            raise AssertionError("lexical UNC path reached Path.is_file")
        return original_is_file(path)

    def is_dir(path: Path) -> bool:
        if remote(path):
            raise AssertionError("lexical UNC path reached Path.is_dir")
        return original_is_dir(path)

    def read_text(path: Path, *args, **kwargs) -> str:
        if remote(path):
            raise AssertionError("lexical UNC path reached Path.read_text")
        return original_read_text(path, *args, **kwargs)

    def lexists(path: object) -> bool:
        if remote(path):
            raise AssertionError("lexical UNC path reached os.path.lexists")
        return original_lexists(path)

    def lstat(path: object, *args, **kwargs):
        if remote(path):
            raise AssertionError("lexical UNC path reached os.lstat")
        return original_lstat(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(Path, "is_file", is_file)
    monkeypatch.setattr(Path, "is_dir", is_dir)
    monkeypatch.setattr(Path, "read_text", read_text)
    monkeypatch.setattr(config_module.os.path, "lexists", lexists)
    monkeypatch.setattr(config_module.os, "lstat", lstat)


@pytest.mark.parametrize(
    "field",
    ["workspace_config", "state_root", "log_root", "frontend_root", "vault_root"],
)
def test_config_rejects_lexical_unc_values_before_filesystem_touch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    payload = _valid_app_config_payload(tmp_path)
    remote = r"\\server\share\research-kb"
    if field == "workspace_config":
        payload["workspaces"][0]["config_path"] = remote + r"\workspace.yaml"
    elif field == "vault_root":
        payload["obsidian_targets"][0]["vault_root"] = remote + r"\vault"
    else:
        payload[field] = remote + "\\" + field
    config_path = tmp_path / f"app-{field}.json"
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    _trap_remote_filesystem_touch(monkeypatch)

    with pytest.raises(AppConfigError, match="must be local"):
        load_app_config(config_path)


@pytest.mark.parametrize(
    "remote",
    [r"\\server\share\app-config.json", r"\\?\UNC\server\share\app-config.json"],
)
def test_config_rejects_lexical_unc_config_path_before_filesystem_touch(
    monkeypatch: pytest.MonkeyPatch, remote: str
) -> None:
    _trap_remote_filesystem_touch(monkeypatch)

    with pytest.raises(AppConfigError, match="configuration path must be local"):
        load_app_config(Path(remote))
