from __future__ import annotations

import argparse
import json
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace-config", required=True, type=Path)
    parser.add_argument("--frontend-root", required=True, type=Path)
    parser.add_argument("--target-root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--option-id", default="p2-small")
    parser.add_argument("--label", default="P2 Small Synthetic")
    args = parser.parse_args()

    target_root = args.target_root.resolve()
    payload = {
        "contract_version": "research-kb-app-config@1.0",
        "workspaces": [
            {
                "option_id": args.option_id,
                "label": args.label,
                "config_path": str(args.workspace_config.resolve()),
            }
        ],
        "state_root": str((target_root / "app-state").resolve()),
        "log_root": str((target_root / "app-state" / "logs").resolve()),
        "frontend_root": str(args.frontend_root.resolve()),
        "request_budgets": {
            "max_body_bytes": 16384,
            "max_query_bytes": 2048,
            "max_page_size": 100,
            "request_timeout_seconds": 30,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
