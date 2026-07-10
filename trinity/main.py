from __future__ import annotations

import sys
from pathlib import Path

# 鲁棒路径注入: 保证仓库根目录在 sys.path 首位
_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

"""Trinity OS v2.1 - 运行时主入口

CLI 入口, 提供 --dry-run 模式。

用法:
    python -m trinity.main --dry-run
    python -m trinity.main --dry-run --symbol 000001 --bars 100
    python -m trinity.main --dry-run --symbol 000001 --save-ledger data/ledger.json
"""
import argparse
import json
import sys
from pathlib import Path
from typing import Optional


def load_config(config_path: str = "config/macro_config.yaml") -> dict:
    """加载配置文件

    优先使用 yaml, 不可用时降级为默认配置。
    """
    config_file = Path(config_path)
    if config_file.exists():
        try:
            import yaml
            with open(config_file, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except ImportError:
            # yaml 不可用时, 返回 None 让 Orchestrator 使用默认值
            return {}
    return {}


def run(
    symbol: str = "DEFAULT",
    bars: int = 100,
    dry_run: bool = True,
    config_path: str = "config/macro_config.yaml",
    save_ledger: Optional[str] = None,
    seed: Optional[int] = None,
    verbose: bool = False,
) -> dict:
    """执行 dry-run, 返回结果字典

    Returns:
        {
            "symbol": str,
            "dry_run": bool,
            "decisions": [...],
            "ledger_summary": {...},
        }
    """
    # 延迟导入, 避免 circular import
    from trinity.orchestrator import Orchestrator

    config = load_config(config_path)
    orch = Orchestrator(config)

    decisions = orch.run(symbol=symbol, bars=bars, dry_run=dry_run, seed=seed)

    # 保存账本
    if save_ledger:
        orch.save_ledger(save_ledger)

    # 构建结果
    result = {
        "symbol": symbol,
        "dry_run": dry_run,
        "decisions": [d.to_dict() for d in decisions],
        "ledger_summary": orch.get_ledger().summary(),
    }

    if verbose:
        _print_result(result)

    return result


def _print_result(result: dict) -> None:
    """格式化输出结果"""
    print("=" * 60)
    print(f"Trinity OS v2.1 | symbol={result['symbol']} | dry_run={result['dry_run']}")
    print("=" * 60)

    for d in result["decisions"]:
        print(f"\n--- 决策: {d['action']} ---")
        print(f"  置信度: {d['confidence']:.2%}")
        print(f"  风险级: {d['risk_level']:.2%}")
        print(f"  时空分: {d['spacetime_overall']:.2f}")
        print(f"  触发级: {d['level']}")
        print(f"  备注:   {d['note']}")
        print(f"  证据链:")
        for e in d["evidence"]:
            print(f"    {e}")

    summary = result["ledger_summary"]
    print(f"\n--- 账本摘要 ---")
    print(f"  事件总数: {summary['total_events']}")
    print(f"  平均置信: {summary['avg_confidence']:.2%}")
    if summary["actions"]:
        print(f"  动作分布: {summary['actions']}")

    print("\n" + "=" * 60)


def main(argv: Optional[list[str]] = None) -> int:
    """CLI 主入口

    Returns:
        0: 成功
        1: 失败
    """
    parser = argparse.ArgumentParser(
        description="Trinity OS v2.1 - 三位一体交易决策操作系统",
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=True,
        help="dry-run 模式 (默认开启, 不执行真实交易)",
    )
    parser.add_argument("--symbol", default="DEFAULT", help="标的代码")
    parser.add_argument("--bars", type=int, default=100, help="K 线数量")
    parser.add_argument("--config", default="config/macro_config.yaml", help="配置文件路径")
    parser.add_argument("--save-ledger", default=None, help="账本保存路径")
    parser.add_argument("--seed", type=int, default=None, help="随机种子 (合成数据)")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")

    args = parser.parse_args(argv)

    try:
        result = run(
            symbol=args.symbol,
            bars=args.bars,
            dry_run=args.dry_run,
            config_path=args.config,
            save_ledger=args.save_ledger,
            seed=args.seed,
            verbose=args.verbose,
        )
        # 验证结果完整性
        if not result.get("decisions"):
            print("ERROR: 未产生任何决策", file=sys.stderr)
            return 1

        # dry-run 成功输出
        if not args.verbose:
            action = result["decisions"][0]["action"]
            conf = result["decisions"][0]["confidence"]
            print(f"[dry-run] {args.symbol}: {action} (confidence={conf:.2%})")

        return 0

    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
