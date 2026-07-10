#!/usr/bin/env python3
"""Trinity OS v2.1 - 账本校验脚本

校验事件溯源账本的完整性与合法性。

用法:
    python scripts/validate_ledger.py
    python scripts/validate_ledger.py --ledger path/to/ledger.json
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# 将项目根目录加入 path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from trinity.ledger import validate_ledger


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Trinity OS 账本校验")
    parser.add_argument(
        "--ledger", default="data/ledger.json",
        help="账本文件路径 (默认 data/ledger.json)",
    )
    args = parser.parse_args(argv)

    filepath = args.ledger
    print(f"账本文件: {filepath}")

    ok, errors = validate_ledger(filepath)

    if ok:
        print("校验结果: ✓ 通过")
        # 输出账本摘要
        try:
            import json
            with open(filepath, "r", encoding="utf-8") as f:
                data = json.load(f)
            events = data.get("events", [])
            print(f"  版本: {data.get('version', 'unknown')}")
            print(f"  事件数: {len(events)}")
            if events:
                actions = {}
                for e in events:
                    a = e.get("decision", {}).get("action", "UNKNOWN")
                    actions[a] = actions.get(a, 0) + 1
                print(f"  动作分布: {actions}")
                symbols = set(e.get("symbol", "") for e in events)
                print(f"  标的数: {len(symbols)}")
        except Exception as e:
            print(f"  (摘要读取失败: {e})")
        return 0
    else:
        print(f"校验结果: ✗ 失败 ({len(errors)} 个错误)")
        for err in errors:
            print(f"  {err}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
