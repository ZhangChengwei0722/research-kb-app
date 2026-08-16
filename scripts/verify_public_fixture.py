from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from tools.public_fixture_contract import (  # noqa: E402
    FixtureContractError,
    default_fixture_root,
    verify_fixture,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the pinned public p2-small fixture.")
    parser.add_argument("--fixture", type=Path, help="workspace payload root to verify")
    args = parser.parse_args()
    configured = os.environ.get("RKB_P2_SMALL_FIXTURE")
    fixture = args.fixture or (Path(configured) if configured else default_fixture_root())
    try:
        verification = verify_fixture(fixture)
    except FixtureContractError as exc:
        print(f"public fixture verification failed: {exc}", file=sys.stderr)
        return 1
    print(
        json.dumps(
            {
                "verified": True,
                "file_count": verification.file_count,
                "total_bytes": verification.total_bytes,
                "whole_tree_digest": verification.whole_tree_digest,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
