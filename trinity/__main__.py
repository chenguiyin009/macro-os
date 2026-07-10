"""Trinity OS v2.1 命令行入口.

用法:
    python -m trinity --dry-run
    python -m trinity --dry-run --symbol 000001 --bars 100
    python -m trinity --dry-run --symbol 000001 --save-ledger data/ledger.json
"""
from trinity.main import main

if __name__ == "__main__":
    raise SystemExit(main())
