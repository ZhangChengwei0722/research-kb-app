from __future__ import annotations

import argparse
import json
from pathlib import Path

from research_kb_app.compatibility import CompatibilityError, build_compatibility_marker


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a Research KB App/Core compatibility marker")
    parser.add_argument("--core-commit", required=True)
    parser.add_argument("--wheel-sha256", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dependency-name", action="append", required=True)
    parser.add_argument(
        "--reviewed-runtime-profile",
        action="append",
        required=True,
        type=Path,
        help="JSON object for the one independently reviewed peer runtime profile",
    )
    parser.add_argument("--replace", action="store_true", help="allow replacing an existing marker")
    args = parser.parse_args()
    if len(args.reviewed_runtime_profile) != 1:
        parser.error("exactly one --reviewed-runtime-profile is required")
    if args.output.exists() and not args.replace:
        parser.error("output exists; pass --replace explicitly")
    try:
        reviewed_profile = json.loads(
            args.reviewed_runtime_profile[0].read_text(encoding="utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        parser.error(f"reviewed runtime profile is unreadable or invalid: {error}")
    if not isinstance(reviewed_profile, dict):
        parser.error("reviewed runtime profile must be one JSON object")
    try:
        marker = build_compatibility_marker(
            core_commit=args.core_commit,
            wheel_sha256=args.wheel_sha256,
            dependency_distribution_names=args.dependency_name,
            additional_dependency_profiles=[reviewed_profile],
        )
    except CompatibilityError as error:
        parser.error(str(error))
    args.output.write_text(
        json.dumps(marker, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
