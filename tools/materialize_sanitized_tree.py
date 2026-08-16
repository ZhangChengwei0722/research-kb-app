from __future__ import annotations

import sys

from public_source_contract import main


if __name__ == "__main__":
    raise SystemExit(main(["materialize", *sys.argv[1:]]))
