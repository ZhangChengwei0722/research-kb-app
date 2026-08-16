from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wheel", required=True, type=Path)
    parser.add_argument(
        "--compatibility",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "core-compatibility.json",
    )
    args = parser.parse_args()
    expected = json.loads(args.compatibility.read_text(encoding="utf-8"))["wheel_sha256"]
    digest = hashlib.sha256(args.wheel.read_bytes()).hexdigest()
    if digest != expected:
        raise SystemExit("Core wheel digest mismatch")
    print(digest)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
