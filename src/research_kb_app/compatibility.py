from __future__ import annotations

import hashlib
import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from email.parser import Parser
from email.policy import compat32
from importlib import metadata, resources
from pathlib import Path, PurePosixPath
from typing import Any

from packaging.markers import default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.tags import sys_tags
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from research_kb.application import APPLICATION_SERVICE_INTERFACE_VERSION
from research_kb.catalog import CATALOG_CONTRACT_VERSION
from research_kb.services import CapabilityService, CatalogCapabilityService


COMPATIBILITY_SCHEMA_VERSION = "research-kb-app-core-compatibility@3"
RUNTIME_PAYLOAD_ALGORITHM = "research-kb-runtime-payload-v1"
REQUIRES_DIST_ALGORITHM = "sha256_compact_json_sorted_requires_dist_v1"
DEPENDENCY_PAYLOAD_ALGORITHM = "research-kb-dependency-payload-v1"
FROZEN_LOCK_ALGORITHM = "sha256_compact_json_sorted_dependency_lock_v1"
CAPABILITY_PROFILE_ALGORITHM = "sha256_compact_json_sorted_capability_profile_v1"
CAPABILITY_PROFILE_ID = "research-kb-capability-profile-core-pdf-beta-v1"
DEPENDENCY_PROFILE_ID = "core_pdf_beta_v1"
REQUIRED_CORE_REQUIREMENT = "research-kb-core[pdf]"
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
COMMIT_PATTERN = re.compile(r"^[0-9a-f]{40}$")
NORMALIZED_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
COMPATIBILITY_KEYS = {
    "application_service_interface_version",
    "catalog_contract_version",
    "capability_profile",
    "compatibility_schema_version",
    "core_commit",
    "dependency_profiles",
    "package_name",
    "package_version",
    "requires_dist",
    "runtime_payload",
    "wheel_sha256",
}
RUNTIME_PAYLOAD_KEYS = {"algorithm", "file_count", "sha256"}
REQUIRES_DIST_KEYS = {"algorithm", "values", "sha256"}
DEPENDENCY_PROFILE_KEYS = {
    "algorithm",
    "distribution_count",
    "distributions",
    "frozen_lock_algorithm",
    "frozen_lock_sha256",
    "installed_payload_algorithm",
    "installed_payload_file_count",
    "installed_payload_sha256",
    "profile_id",
    "required_core_requirement",
    "requires_dist_algorithm",
    "requires_dist_sha256",
}
RUNTIME_SELECTOR_KEYS = {
    "abi_tag",
    "implementation",
    "interpreter_tag",
    "platform_tag",
    "python_minor",
}
DEPENDENCY_RUNTIME_PROFILE_KEYS = DEPENDENCY_PROFILE_KEYS | {"runtime_selector"}
SUPPORTED_RUNTIME_SELECTOR_KEYS = (
    ("cpython", "3.11", "cp311", "cp311", "win_amd64"),
    ("cpython", "3.12", "cp312", "cp312", "win_amd64"),
)
CAPABILITY_PROFILE_KEYS = {"algorithm", "profile_id", "selected_facts", "sha256"}
_GENERATED_FILE_NAMES = {"RECORD", "INSTALLER", "REQUESTED", "direct_url.json"}
_PYTHON_CACHE_SUFFIXES = {".pyc", ".pyo"}
_REQUIRED_ADAPTERS = ("pdfplumber", "pdfplumber-text-flow")
_REQUIRED_TRUSTED_PARSE_FEATURES = (
    "trusted_parse_authority",
    "supervised_pdf_parse",
    "trusted_parse_intake_application",
)
_REQUIRED_CAPABILITY_PROFILE_FEATURES = (
    *_REQUIRED_TRUSTED_PARSE_FEATURES,
    "intake_source_adequacy_resolution",
)


class CompatibilityError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CoreCompatibility:
    application_service_interface_version: str
    catalog_contract_version: str
    capability_profile: dict[str, Any]
    compatibility_schema_version: str
    core_commit: str
    dependency_profiles: list[dict[str, Any]]
    package_name: str
    package_version: str
    requires_dist: dict[str, Any]
    runtime_payload: dict[str, Any]
    wheel_sha256: str

    def public_facts(self) -> dict[str, str]:
        return {
            "application_service_interface_version": self.application_service_interface_version,
            "catalog_contract_version": self.catalog_contract_version,
            "core_commit": self.core_commit,
            "package_version": self.package_version,
        }


def load_compatibility(path: Path | None = None) -> CoreCompatibility:
    if path is None:
        packaged = resources.files("research_kb_app").joinpath("core-compatibility.json")
        if packaged.is_file():
            payload = json.loads(packaged.read_text(encoding="utf-8"))
        else:
            development_path = Path(__file__).resolve().parents[2] / "core-compatibility.json"
            payload = json.loads(development_path.read_text(encoding="utf-8"))
    else:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    _validate_marker_shape(payload)
    return CoreCompatibility(**payload)


def verify_installed_core(compatibility: CoreCompatibility) -> dict[str, str]:
    try:
        distribution = metadata.distribution(compatibility.package_name)
        installed_version = distribution.version
    except metadata.PackageNotFoundError as error:
        raise CompatibilityError("Required Research KB Core package is not installed") from error
    actual = {
        "package_version": installed_version,
        "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
        "catalog_contract_version": CATALOG_CONTRACT_VERSION,
    }
    expected = {
        "package_version": compatibility.package_version,
        "application_service_interface_version": compatibility.application_service_interface_version,
        "catalog_contract_version": compatibility.catalog_contract_version,
    }
    if actual != expected:
        raise CompatibilityError("Installed Research KB Core is incompatible with this App build")

    runtime_payload = runtime_payload_facts(distribution)
    if runtime_payload != compatibility.runtime_payload:
        raise CompatibilityError("Installed Research KB Core package payload is stale or incompatible")

    requires_dist = requires_dist_facts(distribution)
    if requires_dist != compatibility.requires_dist:
        raise CompatibilityError("Installed Core Requires-Dist declarations are stale or incompatible")

    runtime_selector = runtime_selector_facts()
    profile_matches = [
        item
        for item in compatibility.dependency_profiles
        if item["runtime_selector"] == runtime_selector
    ]
    if len(profile_matches) != 1:
        raise CompatibilityError("Installed runtime dependency profile is unavailable or ambiguous")
    expected_dependency_profile = {
        key: value for key, value in profile_matches[0].items() if key != "runtime_selector"
    }
    dependency_profile = dependency_profile_facts(
        [item["name"] for item in expected_dependency_profile["distributions"]],
        profile_id=expected_dependency_profile["profile_id"],
        requires_dist=requires_dist,
    )
    if dependency_profile != expected_dependency_profile:
        raise CompatibilityError("Installed Core dependency profile is stale or incompatible")

    capabilities = _load_capability_report()
    _require_capability_features(capabilities)
    pdf_version = _dependency_version(dependency_profile, "pdfplumber")
    capability_profile = capability_profile_facts(
        capabilities,
        dependency_profile["profile_id"],
        expected_core_version=compatibility.package_version,
        expected_pdf_version=pdf_version,
    )
    if capability_profile != compatibility.capability_profile:
        raise CompatibilityError("Installed Core capability profile is stale or incompatible")

    catalog = _load_catalog_report()
    if "tag_id" not in catalog.get("query_filters", []):
        raise CompatibilityError("Installed Research KB Core lacks the Tag Catalog filter")
    return actual


def build_compatibility_marker(
    *,
    core_commit: str,
    wheel_sha256: str,
    dependency_distribution_names: Iterable[str],
    additional_dependency_profiles: Iterable[Mapping[str, Any]],
    package_name: str = "research-kb-core",
) -> dict[str, Any]:
    if not COMMIT_PATTERN.fullmatch(core_commit) or not SHA256_PATTERN.fullmatch(wheel_sha256):
        raise CompatibilityError("Core source commit or wheel digest is invalid")
    distribution = _load_distribution(package_name)
    requires_dist = requires_dist_facts(distribution)
    capabilities = CapabilityService().show()
    dependency_profile = dependency_profile_facts(
        dependency_distribution_names,
        profile_id=DEPENDENCY_PROFILE_ID,
        requires_dist=requires_dist,
    )
    dependency_profiles = [
        {
            **dependency_profile,
            "runtime_selector": runtime_selector_facts(),
        },
        *(dict(item) for item in additional_dependency_profiles),
    ]
    dependency_profiles.sort(key=lambda item: _runtime_selector_key(item.get("runtime_selector")))
    marker = {
        "application_service_interface_version": APPLICATION_SERVICE_INTERFACE_VERSION,
        "catalog_contract_version": CATALOG_CONTRACT_VERSION,
        "capability_profile": capability_profile_facts(
            capabilities,
            dependency_profile["profile_id"],
            expected_core_version=distribution.version,
            expected_pdf_version=_dependency_version(dependency_profile, "pdfplumber"),
        ),
        "compatibility_schema_version": COMPATIBILITY_SCHEMA_VERSION,
        "core_commit": core_commit,
        "dependency_profiles": dependency_profiles,
        "package_name": distribution.metadata["Name"] or package_name,
        "package_version": distribution.version,
        "requires_dist": requires_dist,
        "runtime_payload": runtime_payload_facts(distribution),
        "wheel_sha256": wheel_sha256,
    }
    _validate_marker_shape(marker)
    return marker


def runtime_selector_facts() -> dict[str, str]:
    try:
        primary_tag = next(sys_tags())
    except StopIteration as error:
        raise CompatibilityError("Installed Python runtime tag is unavailable") from error
    return {
        "abi_tag": primary_tag.abi,
        "implementation": sys.implementation.name,
        "interpreter_tag": primary_tag.interpreter,
        "platform_tag": primary_tag.platform,
        "python_minor": f"{sys.version_info.major}.{sys.version_info.minor}",
    }


def runtime_payload_facts(distribution: metadata.Distribution) -> dict[str, Any]:
    records: list[str] = []
    environment_root = _distribution_environment_root(distribution)
    package_root = Path(distribution.locate_file(PurePosixPath("research_kb")))
    resolved_package_root = package_root.resolve()
    if not resolved_package_root.is_relative_to(environment_root) or not package_root.is_dir():
        raise CompatibilityError("Installed Core package directory is unavailable")
    if package_root.is_symlink():
        raise CompatibilityError("Installed Core package escapes its environment")
    for path in sorted(package_root.rglob("*"), key=lambda item: item.as_posix()):
        relative = PurePosixPath(path.relative_to(environment_root).as_posix())
        if path.is_symlink() or not path.resolve().is_relative_to(environment_root):
            raise CompatibilityError("Installed Core package escapes its environment")
        if not path.is_file() or _is_generated_or_cache(relative):
            continue
        records.append(f"{relative.as_posix()}\t{path.stat().st_size}\t{_sha256_file(path)}")
    return {
        "algorithm": RUNTIME_PAYLOAD_ALGORITHM,
        "file_count": len(records),
        "sha256": _digest_records(records),
    }


def requires_dist_facts(distribution: metadata.Distribution) -> dict[str, Any]:
    metadata_text = distribution.read_text("METADATA")
    if not isinstance(metadata_text, str) or not metadata_text:
        raise CompatibilityError("Installed Core METADATA is unavailable")
    message = Parser(policy=compat32).parsestr(metadata_text)
    values = message.get_all("Requires-Dist", []) or []
    if not all(isinstance(value, str) for value in values):
        raise CompatibilityError("Installed Core Requires-Dist declarations are invalid")
    ordered = sorted(values)
    return {
        "algorithm": REQUIRES_DIST_ALGORITHM,
        "values": ordered,
        "sha256": _sha256_bytes(_canonical_json_bytes(ordered)),
    }


def dependency_profile_facts(
    distribution_names: Iterable[str],
    *,
    profile_id: str = DEPENDENCY_PROFILE_ID,
    requires_dist: Mapping[str, Any],
) -> dict[str, Any]:
    raw_names = list(distribution_names)
    if not raw_names or any(not isinstance(name, str) for name in raw_names):
        raise CompatibilityError("Core dependency profile has an invalid distribution set")
    names = [_normalize_distribution_name(name) for name in raw_names]
    if any(not NORMALIZED_NAME_PATTERN.fullmatch(name) for name in names):
        raise CompatibilityError("Core dependency profile has an invalid distribution set")
    if len(names) != len(set(names)):
        raise CompatibilityError("Core dependency profile contains duplicate distributions")
    names.sort()
    expected_names = list(dependency_closure_names("research-kb-core", extras=("pdf",)))
    if names != expected_names:
        raise CompatibilityError("Core dependency profile does not match the required closure")
    records: list[str] = []
    distributions: list[dict[str, Any]] = []
    for name in names:
        distribution = _load_distribution(name)
        actual_name = distribution.metadata["Name"]
        if not isinstance(actual_name, str) or _normalize_distribution_name(actual_name) != name:
            raise CompatibilityError("Core dependency distribution identity is invalid")
        distribution_records: list[str] = []
        for relative, path in _distribution_files(distribution):
            if _is_generated_or_cache(relative):
                continue
            record = f"{relative.as_posix()}\t{path.stat().st_size}\t{_sha256_file(path)}"
            distribution_records.append(record)
            records.append(f"{name}\t{record}")
        distributions.append(
            {
                "file_count": len(distribution_records),
                "installed_payload_sha256": _digest_records(distribution_records),
                "name": name,
                "version": distribution.version,
            }
        )
    frozen_lock = [
        {
            "installed_payload_sha256": item["installed_payload_sha256"],
            "name": item["name"],
            "version": item["version"],
        }
        for item in distributions
    ]
    return {
        "algorithm": DEPENDENCY_PAYLOAD_ALGORITHM,
        "distribution_count": len(distributions),
        "distributions": distributions,
        "frozen_lock_algorithm": FROZEN_LOCK_ALGORITHM,
        "frozen_lock_sha256": _sha256_bytes(_canonical_json_bytes(frozen_lock, sort_keys=True)),
        "installed_payload_algorithm": DEPENDENCY_PAYLOAD_ALGORITHM,
        "installed_payload_file_count": len(records),
        "installed_payload_sha256": _digest_records(records),
        "profile_id": profile_id,
        "required_core_requirement": REQUIRED_CORE_REQUIREMENT,
        "requires_dist_algorithm": requires_dist["algorithm"],
        "requires_dist_sha256": requires_dist["sha256"],
    }


def capability_profile_facts(
    report: Mapping[str, Any],
    dependency_profile_id: str,
    *,
    expected_core_version: str,
    expected_pdf_version: str,
) -> dict[str, Any]:
    if not isinstance(report, Mapping):
        raise CompatibilityError("Core capability report is invalid")
    adapters = report.get("parse_adapters")
    if not isinstance(adapters, list):
        raise CompatibilityError("Core capability adapter facts are unavailable")
    selected_adapters: list[dict[str, Any]] = []
    for adapter_name in _REQUIRED_ADAPTERS:
        matches = [item for item in adapters if isinstance(item, dict) and item.get("adapter") == adapter_name]
        if len(matches) != 1:
            raise CompatibilityError("Core capability adapter identity is ambiguous")
        item = matches[0]
        if (
            item.get("availability") != "available"
            or item.get("diagnostic_code") is not None
            or item.get("version") != expected_pdf_version
        ):
            raise CompatibilityError("Core capability adapter is unavailable or incompatible")
        selected_adapters.append(
            {
                "adapter": adapter_name,
                "availability": item.get("availability"),
                "diagnostic_code": item.get("diagnostic_code"),
                "version": item.get("version"),
            }
        )
    core = report.get("core")
    features = report.get("features")
    if not isinstance(core, Mapping) or not isinstance(features, Mapping):
        raise CompatibilityError("Core capability core facts are unavailable")
    selected = {
        "capability_status": report.get("status"),
        "core_version": core.get("version"),
        "interface_version": report.get("interface_version"),
        "parse_adapters": selected_adapters,
        "real_pdf_parse": features.get("real_pdf_parse"),
        "required_dependency_profile_id": dependency_profile_id,
        **{name: features.get(name) for name in _REQUIRED_CAPABILITY_PROFILE_FEATURES},
    }
    if (
        selected["capability_status"] != "success"
        or selected["core_version"] != expected_core_version
        or selected["interface_version"] != "1.0"
        or selected["real_pdf_parse"] is not True
        or any(selected[name] is not True for name in _REQUIRED_CAPABILITY_PROFILE_FEATURES)
    ):
        raise CompatibilityError("Core capability identity is invalid")
    return {
        "algorithm": CAPABILITY_PROFILE_ALGORITHM,
        "profile_id": CAPABILITY_PROFILE_ID,
        "selected_facts": selected,
        "sha256": _sha256_bytes(_canonical_json_bytes(selected, sort_keys=True)),
    }


def verify_core_wheel(path: Path, compatibility: CoreCompatibility) -> str:
    wheel = Path(path)
    if not wheel.is_file():
        raise CompatibilityError("Reviewed Core wheel is unavailable")
    digest = _sha256_file(wheel)
    if digest != compatibility.wheel_sha256:
        raise CompatibilityError("Reviewed Core wheel digest does not match the compatibility record")
    return digest


def _require_capability_features(capabilities: Mapping[str, Any]) -> None:
    features = capabilities.get("features", {})
    if not isinstance(features, Mapping):
        raise CompatibilityError("Installed Core capability feature facts are unavailable")
    required = {
        "reading_evidence_source_access": "Evidence source access",
        "knowledge_query_agent_tasks": "Knowledge Query Agent Tasks",
        "discovery_application_service": "the Discovery Application Service",
        "tag_application": "the Tag Application Service",
        "question_screening_agent_tasks": "Question Screening Agent Tasks",
        "research_synthesis_application": "the Research Synthesis Application Service",
        "research_synthesis_agent_tasks": "Research Synthesis Agent Tasks",
        "obsidian_generated_views": "Obsidian Generated Views",
        "exchange_source_free_export": "source-free Exchange export",
        "exchange_source_inclusive_export": "source-inclusive Exchange export",
        "exchange_import": "Exchange import",
        "backup_restore": "backup and restore",
        "operational_maintenance": "operational maintenance",
        "lazy_stale_maintenance": "lazy stale maintenance",
        "trusted_parse_authority": "trusted Parse authority",
        "supervised_pdf_parse": "supervised PDF Parse",
        "trusted_parse_intake_application": "the trusted Parse intake Application Service",
        "intake_source_adequacy_resolution": "the intake Source Adequacy resolution Application Service",
    }
    for feature, label in required.items():
        if features.get(feature) is not True:
            raise CompatibilityError(f"Installed Research KB Core lacks {label}")


def _load_capability_report() -> Mapping[str, Any]:
    try:
        report = CapabilityService().show()
    except CompatibilityError:
        raise
    except Exception as error:
        raise CompatibilityError(
            "Installed Research KB Core capability report is unavailable"
        ) from error
    if not isinstance(report, Mapping):
        raise CompatibilityError("Installed Research KB Core capability report is invalid")
    return report


def _load_catalog_report() -> Mapping[str, Any]:
    try:
        report = CatalogCapabilityService().show()
    except CompatibilityError:
        raise
    except Exception as error:
        raise CompatibilityError(
            "Installed Research KB Core catalog report is unavailable"
        ) from error

    if not isinstance(report, Mapping):
        raise CompatibilityError("Installed Research KB Core catalog report is invalid")

    query_filters = report.get("query_filters")
    if (
        not isinstance(query_filters, list)
        or not query_filters
        or any(not isinstance(item, str) or not item for item in query_filters)
    ):
        raise CompatibilityError("Installed Research KB Core catalog report is invalid")

    return report


def _validate_marker_shape(payload: Any) -> None:
    if not isinstance(payload, dict) or set(payload) != COMPATIBILITY_KEYS:
        raise CompatibilityError("Core compatibility record has an invalid shape")
    for key in (
        "application_service_interface_version",
        "catalog_contract_version",
        "compatibility_schema_version",
        "core_commit",
        "package_name",
        "package_version",
    ):
        if not isinstance(payload[key], str) or not payload[key]:
            raise CompatibilityError("Core compatibility record contains an invalid value")
    if payload["compatibility_schema_version"] != COMPATIBILITY_SCHEMA_VERSION:
        raise CompatibilityError("Core compatibility record version is unsupported")
    if not COMMIT_PATTERN.fullmatch(payload["core_commit"]):
        raise CompatibilityError("Core commit identity is invalid")
    if not SHA256_PATTERN.fullmatch(payload["wheel_sha256"]):
        raise CompatibilityError("Core wheel digest is invalid")
    if _normalize_distribution_name(payload["package_name"]) != "research-kb-core":
        raise CompatibilityError("Core package identity is invalid")
    _validate_profile(payload["runtime_payload"], RUNTIME_PAYLOAD_KEYS, RUNTIME_PAYLOAD_ALGORITHM)
    _validate_profile(payload["requires_dist"], REQUIRES_DIST_KEYS, REQUIRES_DIST_ALGORITHM)
    requires = payload["requires_dist"]["values"]
    if not isinstance(requires, list) or not all(isinstance(item, str) for item in requires):
        raise CompatibilityError("Core Requires-Dist profile is invalid")
    if requires != sorted(requires):
        raise CompatibilityError("Core Requires-Dist profile is not canonical")
    if payload["requires_dist"]["sha256"] != _sha256_bytes(_canonical_json_bytes(requires)):
        raise CompatibilityError("Core Requires-Dist profile digest is invalid")
    dependencies = payload["dependency_profiles"]
    if not isinstance(dependencies, list) or not dependencies:
        raise CompatibilityError("Core dependency runtime profiles are invalid")
    selectors: list[tuple[str, ...]] = []
    payload_digests: list[str] = []
    semantic_identities: list[tuple[Any, ...]] = []
    for runtime_profile in dependencies:
        if not isinstance(runtime_profile, dict) or set(runtime_profile) != DEPENDENCY_RUNTIME_PROFILE_KEYS:
            raise CompatibilityError("Core dependency runtime profile has an invalid shape")
        _validate_runtime_selector(runtime_profile["runtime_selector"])
        dependency = _dependency_profile_payload(runtime_profile)
        _validate_dependency_profile(
            dependency,
            requires_dist_sha256=payload["requires_dist"]["sha256"],
        )
        selectors.append(_runtime_selector_key(runtime_profile["runtime_selector"]))
        payload_digests.append(dependency["installed_payload_sha256"])
        semantic_identities.append(
            (
                dependency["profile_id"],
                dependency["required_core_requirement"],
                dependency["requires_dist_algorithm"],
                dependency["requires_dist_sha256"],
                tuple(
                    (item["name"], item["version"], item["file_count"])
                    for item in dependency["distributions"]
                ),
            )
        )
    if tuple(selectors) != SUPPORTED_RUNTIME_SELECTOR_KEYS:
        raise CompatibilityError("Core dependency runtime profile set is unsupported or noncanonical")
    if len(payload_digests) != len(set(payload_digests)):
        raise CompatibilityError("Core dependency runtime payload profiles are duplicated")
    if len(set(semantic_identities)) != 1:
        raise CompatibilityError("Core dependency runtime profiles do not share one semantic closure")
    dependency = _dependency_profile_payload(dependencies[0])
    _validate_profile(payload["capability_profile"], CAPABILITY_PROFILE_KEYS, CAPABILITY_PROFILE_ALGORITHM)
    capability = payload["capability_profile"]
    if capability["profile_id"] != CAPABILITY_PROFILE_ID:
        raise CompatibilityError("Core capability profile ID is invalid")
    selected = capability["selected_facts"]
    selected_keys = {
        "capability_status",
        "core_version",
        "interface_version",
        "parse_adapters",
        "real_pdf_parse",
        "required_dependency_profile_id",
        *_REQUIRED_CAPABILITY_PROFILE_FEATURES,
    }
    if not isinstance(selected, dict) or set(selected) != selected_keys:
        raise CompatibilityError("Core selected capability facts are invalid")
    if (
        selected["capability_status"] != "success"
        or selected["real_pdf_parse"] is not True
        or any(selected[name] is not True for name in _REQUIRED_CAPABILITY_PROFILE_FEATURES)
    ):
        raise CompatibilityError("Core selected capability status is invalid")
    if selected["interface_version"] != "1.0":
        raise CompatibilityError("Core selected capability interface is invalid")
    if selected["core_version"] != payload["package_version"]:
        raise CompatibilityError("Core selected capability version is inconsistent")
    if selected["required_dependency_profile_id"] != dependency["profile_id"]:
        raise CompatibilityError("Core selected capability dependency profile is inconsistent")
    adapters = selected["parse_adapters"]
    if not isinstance(adapters, list) or [item.get("adapter") for item in adapters if isinstance(item, dict)] != list(
        _REQUIRED_ADAPTERS
    ):
        raise CompatibilityError("Core selected capability adapter order is invalid")
    for adapter in adapters:
        if not isinstance(adapter, dict) or set(adapter) != {
            "adapter",
            "availability",
            "diagnostic_code",
            "version",
        }:
            raise CompatibilityError("Core selected capability adapter facts are invalid")
        if adapter["availability"] != "available" or adapter["diagnostic_code"] is not None:
            raise CompatibilityError("Core selected capability adapter status is invalid")
        if not isinstance(adapter["version"], str) or not adapter["version"]:
            raise CompatibilityError("Core selected capability adapter version is invalid")
        if adapter["version"] != _dependency_version(dependency, "pdfplumber"):
            raise CompatibilityError("Core selected capability adapter version is inconsistent")
    if capability["sha256"] != _sha256_bytes(_canonical_json_bytes(selected, sort_keys=True)):
        raise CompatibilityError("Core capability profile digest is invalid")


def _validate_dependency_profile(
    dependency: Mapping[str, Any],
    *,
    requires_dist_sha256: str,
) -> None:
    _validate_profile(
        dependency,
        DEPENDENCY_PROFILE_KEYS,
        DEPENDENCY_PAYLOAD_ALGORITHM,
        digest_key="installed_payload_sha256",
    )
    if dependency["profile_id"] != DEPENDENCY_PROFILE_ID:
        raise CompatibilityError("Core dependency profile ID is invalid")
    if not isinstance(dependency["distribution_count"], int) or dependency["distribution_count"] <= 0:
        raise CompatibilityError("Core dependency profile distribution count is invalid")
    if not isinstance(dependency["distributions"], list) or not dependency["distributions"]:
        raise CompatibilityError("Core dependency profile distributions are invalid")
    if dependency["distribution_count"] != len(dependency["distributions"]):
        raise CompatibilityError("Core dependency profile distribution count is inconsistent")
    names: list[str] = []
    for item in dependency["distributions"]:
        if not isinstance(item, dict) or set(item) != {
            "file_count",
            "installed_payload_sha256",
            "name",
            "version",
        }:
            raise CompatibilityError("Core dependency profile distribution identity is invalid")
        if not isinstance(item["name"], str) or not NORMALIZED_NAME_PATTERN.fullmatch(item["name"]):
            raise CompatibilityError("Core dependency profile distribution name is invalid")
        if not isinstance(item["version"], str) or not item["version"]:
            raise CompatibilityError("Core dependency profile distribution version is invalid")
        if not isinstance(item["file_count"], int) or item["file_count"] < 0:
            raise CompatibilityError("Core dependency profile distribution file count is invalid")
        if not isinstance(item["installed_payload_sha256"], str) or not SHA256_PATTERN.fullmatch(
            item["installed_payload_sha256"]
        ):
            raise CompatibilityError("Core dependency profile distribution digest is invalid")
        names.append(item["name"])
    if names != sorted(names) or len(names) != len(set(names)):
        raise CompatibilityError("Core dependency profile distribution order is not canonical")
    if dependency["installed_payload_file_count"] != sum(
        item["file_count"] for item in dependency["distributions"]
    ):
        raise CompatibilityError("Core dependency profile payload file count is inconsistent")
    if dependency["frozen_lock_algorithm"] != FROZEN_LOCK_ALGORITHM:
        raise CompatibilityError("Core dependency profile frozen lock algorithm is invalid")
    frozen_lock = [
        {
            "installed_payload_sha256": item["installed_payload_sha256"],
            "name": item["name"],
            "version": item["version"],
        }
        for item in dependency["distributions"]
    ]
    if dependency["frozen_lock_sha256"] != _sha256_bytes(
        _canonical_json_bytes(frozen_lock, sort_keys=True)
    ):
        raise CompatibilityError("Core dependency profile frozen lock digest is invalid")
    if dependency["installed_payload_algorithm"] != DEPENDENCY_PAYLOAD_ALGORITHM:
        raise CompatibilityError("Core dependency profile payload algorithm is invalid")
    if not isinstance(dependency["installed_payload_file_count"], int) or dependency[
        "installed_payload_file_count"
    ] < 0:
        raise CompatibilityError("Core dependency profile payload file count is invalid")
    if dependency["required_core_requirement"] != REQUIRED_CORE_REQUIREMENT:
        raise CompatibilityError("Core dependency profile required Core requirement is invalid")
    if dependency["requires_dist_algorithm"] != REQUIRES_DIST_ALGORITHM:
        raise CompatibilityError("Core dependency profile Requires-Dist algorithm is invalid")
    if dependency["requires_dist_sha256"] != requires_dist_sha256:
        raise CompatibilityError("Core dependency profile Requires-Dist digest is inconsistent")


def _dependency_profile_payload(runtime_profile: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in runtime_profile.items() if key != "runtime_selector"}


def _validate_runtime_selector(value: Any) -> None:
    if not isinstance(value, dict) or set(value) != RUNTIME_SELECTOR_KEYS:
        raise CompatibilityError("Core dependency runtime selector has an invalid shape")
    if not all(isinstance(value[key], str) and value[key] for key in RUNTIME_SELECTOR_KEYS):
        raise CompatibilityError("Core dependency runtime selector contains an invalid value")
    if not re.fullmatch(r"[0-9]+\.[0-9]+", value["python_minor"]):
        raise CompatibilityError("Core dependency runtime selector Python minor is invalid")
    for key in ("abi_tag", "implementation", "interpreter_tag", "platform_tag"):
        if not re.fullmatch(r"[a-z0-9_]+", value[key]):
            raise CompatibilityError("Core dependency runtime selector tag is invalid")


def _runtime_selector_key(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Mapping):
        return ("", "", "", "", "")
    return tuple(
        str(value.get(key, ""))
        for key in ("implementation", "python_minor", "interpreter_tag", "abi_tag", "platform_tag")
    )


def _validate_profile(
    value: Any,
    keys: set[str],
    algorithm: str,
    *,
    digest_key: str = "sha256",
) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise CompatibilityError("Core compatibility profile has an invalid shape")
    if not isinstance(value.get("algorithm"), str) or value["algorithm"] != algorithm:
        raise CompatibilityError("Core compatibility profile algorithm is unsupported")
    if "file_count" in value and (
        not isinstance(value["file_count"], int) or value["file_count"] < 0
    ):
        raise CompatibilityError("Core compatibility profile file count is invalid")
    digest = value.get(digest_key)
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        raise CompatibilityError("Core compatibility profile digest is invalid")


def _load_distribution(name: str) -> metadata.Distribution:
    matches = _find_distributions(name)
    if not matches:
        raise CompatibilityError(f"Required Core distribution {name!r} is unavailable")
    if len(matches) > 1:
        raise CompatibilityError(f"Required Core distribution {name!r} is installed more than once")
    return matches[0]


def _find_distributions(name: str) -> list[metadata.Distribution]:
    normalized = _normalize_distribution_name(name)
    matches: list[metadata.Distribution] = []
    for distribution in metadata.distributions(name=name):
        actual_name = distribution.metadata.get("Name")
        if isinstance(actual_name, str) and _normalize_distribution_name(actual_name) == normalized:
            matches.append(distribution)
    return matches


def dependency_closure_names(
    package_name: str,
    *,
    extras: Iterable[str] = (),
) -> tuple[str, ...]:
    """Resolve the active PEP 508 dependency closure in the current interpreter.

    The caller's roster is deliberately not used for resolution. It is only compared
    with this result by ``dependency_profile_facts`` so a hand-written or incomplete
    marker cannot define its own dependency boundary.
    """
    root = _normalize_distribution_name(package_name)
    active_extras = frozenset(extras)
    if any(not isinstance(extra, str) or not extra for extra in active_extras):
        raise CompatibilityError("Core dependency extras are invalid")
    pending: list[tuple[str, frozenset[str]]] = [(root, active_extras)]
    processed: dict[str, set[str]] = {}
    closure: set[str] = set()
    environment = default_environment()

    while pending:
        name, requested_extras = pending.pop()
        already = processed.get(name)
        if already is not None and requested_extras <= already:
            continue
        if already is None:
            already = set()
            processed[name] = already
        new_extras = requested_extras - already
        already.update(new_extras)
        distribution = _load_distribution(name)
        try:
            distribution_version = Version(distribution.version)
        except InvalidVersion as error:
            raise CompatibilityError(f"Core dependency {name!r} has an invalid version") from error
        if name != root:
            closure.add(name)
        for raw_requirement in distribution.requires or ():
            try:
                requirement = Requirement(raw_requirement)
            except InvalidRequirement as error:
                raise CompatibilityError("Core dependency metadata contains an invalid requirement") from error
            marker_environment = dict(environment)
            marker_environment["extra"] = ""
            marker_active = requirement.marker is None
            marker_extras = requested_extras or frozenset({""})
            for extra in marker_extras:
                marker_environment["extra"] = extra
                if requirement.marker is None or requirement.marker.evaluate(marker_environment):
                    marker_active = True
                    break
            if not marker_active:
                continue
            dependency_name = canonicalize_name(requirement.name)
            dependency = _load_distribution(dependency_name)
            try:
                dependency_version = Version(dependency.version)
            except InvalidVersion as error:
                raise CompatibilityError(
                    f"Core dependency {dependency_name!r} has an invalid version"
                ) from error
            if requirement.specifier and not requirement.specifier.contains(
                dependency_version, prereleases=True
            ):
                raise CompatibilityError(f"Core dependency {dependency_name!r} does not satisfy its requirement")
            pending.append((dependency_name, frozenset(requirement.extras)))

    return tuple(sorted(closure))


def _distribution_files(distribution: metadata.Distribution) -> list[tuple[PurePosixPath, Path]]:
    entries = distribution.files
    if entries is None:
        raise CompatibilityError("Installed distribution file manifest is unavailable")
    environment_root = _distribution_environment_root(distribution)
    result: list[tuple[PurePosixPath, Path]] = []
    for entry in entries:
        relative = PurePosixPath(str(entry))
        if relative.is_absolute() or not relative.parts:
            raise CompatibilityError("Installed distribution contains an unsafe path")
        path = Path(distribution.locate_file(entry))
        resolved = path.resolve()
        if ".." in relative.parts and not _is_environment_entrypoint(relative, resolved, environment_root):
            raise CompatibilityError("Installed distribution contains an unsafe path")
        if not resolved.is_relative_to(environment_root):
            if _is_environment_entrypoint(relative, resolved, environment_root):
                continue
            raise CompatibilityError("Installed distribution escapes its environment")
        if path.is_symlink():
            raise CompatibilityError("Installed distribution escapes its environment")
        if not path.is_file():
            raise CompatibilityError("Installed distribution file is missing")
        if _is_generated_or_cache(relative):
            continue
        result.append((relative, path))
    return sorted(result, key=lambda item: item[0].as_posix())


def _distribution_environment_root(distribution: metadata.Distribution) -> Path:
    root = Path(distribution.locate_file(PurePosixPath("."))).resolve()
    if not root.is_dir():
        raise CompatibilityError("Installed distribution environment is unavailable")
    return root


def _is_generated_or_cache(relative: PurePosixPath) -> bool:
    return (
        "__pycache__" in relative.parts
        or relative.suffix.lower() in _PYTHON_CACHE_SUFFIXES
        or relative.name in _GENERATED_FILE_NAMES
    )


def _is_environment_entrypoint(relative: PurePosixPath, resolved: Path, environment_root: Path) -> bool:
    scripts_root = environment_root.parent.parent / "Scripts"
    return (
        ".." in relative.parts
        and resolved.is_relative_to(scripts_root)
        and resolved.suffix.lower() in {".exe", ".cmd", ".bat", ".ps1", ".py", ".pyc", ".pyo"}
    )


def _normalize_distribution_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _canonical_json_bytes(value: Any, *, sort_keys: bool = False) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=sort_keys).encode("utf-8")


def _dependency_version(profile: Mapping[str, Any], name: str) -> str:
    normalized = _normalize_distribution_name(name)
    distributions = profile.get("distributions")
    if not isinstance(distributions, list):
        raise CompatibilityError("Core dependency profile distributions are invalid")
    matches = [
        item
        for item in distributions
        if isinstance(item, Mapping) and item.get("name") == normalized
    ]
    if len(matches) != 1 or not isinstance(matches[0].get("version"), str):
        raise CompatibilityError(f"Core dependency {name!r} is not uniquely profiled")
    return matches[0]["version"]


def _digest_records(records: Sequence[str]) -> str:
    payload = "".join(f"{record}\n" for record in sorted(records)).encode("utf-8")
    return _sha256_bytes(payload)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def compatibility_payload(value: CoreCompatibility) -> dict[str, Any]:
    return value.public_facts()


__all__ = [
    "CAPABILITY_PROFILE_ALGORITHM",
    "CAPABILITY_PROFILE_ID",
    "COMPATIBILITY_SCHEMA_VERSION",
    "DEPENDENCY_PAYLOAD_ALGORITHM",
    "DEPENDENCY_PROFILE_ID",
    "REQUIRES_DIST_ALGORITHM",
    "RUNTIME_PAYLOAD_ALGORITHM",
    "CompatibilityError",
    "CoreCompatibility",
    "build_compatibility_marker",
    "capability_profile_facts",
    "compatibility_payload",
    "dependency_closure_names",
    "dependency_profile_facts",
    "load_compatibility",
    "requires_dist_facts",
    "runtime_selector_facts",
    "runtime_payload_facts",
    "verify_core_wheel",
    "verify_installed_core",
]
