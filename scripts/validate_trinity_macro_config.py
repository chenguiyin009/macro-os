#!/usr/bin/env python3
"""Trinity OS v2.1 - 配置校验脚本

校验 macro_config.yaml 的完整性与合法性。

用法:
    python scripts/validate_macro_config.py
    python scripts/validate_macro_config.py --config path/to/config.yaml
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path


def validate_config(config_path: str = "config/macro_config.yaml") -> tuple[bool, list[str]]:
    """校验配置文件

    检查项:
      1. 文件存在且可解析
      2. levels 定义完整 (J+2/J/J-1/J-2)
      3. indicators 参数合法
      4. spacetime 约束合理
      5. state_machine 六态定义完整
      6. decision_matrix 覆盖所有状态×方向组合
      7. risk 参数合理
    """
    errors: list[str] = []
    warnings: list[str] = []

    config_file = Path(config_path)
    if not config_file.exists():
        return False, [f"配置文件不存在: {config_path}"]

    try:
        import yaml
        with open(config_file, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except ImportError:
        return False, ["PyYAML 未安装, 无法解析 YAML 配置"]
    except Exception as e:
        return False, [f"YAML 解析失败: {e}"]

    if not isinstance(config, dict):
        return False, ["配置根节点应为字典"]

    # 1. levels
    levels = config.get("levels", {})
    required_levels = {"J_PLUS_2", "J", "J_MINUS_1", "J_MINUS_2"}
    missing = required_levels - set(levels.keys())
    if missing:
        errors.append(f"levels 缺少: {missing}")
    for key, val in levels.items():
        if not isinstance(val, dict):
            errors.append(f"levels.{key} 应为字典")
            continue
        if "name" not in val or "timeframe" not in val:
            errors.append(f"levels.{key} 缺少 name 或 timeframe")
        if "role" not in val:
            warnings.append(f"levels.{key} 缺少 role 定义")

    # 2. indicators
    ind = config.get("indicators", {})
    ma = ind.get("ma", {})
    if not ma.get("periods"):
        errors.append("indicators.ma.periods 不能为空")
    else:
        for p in ma["periods"]:
            if not isinstance(p, int) or p <= 0:
                errors.append(f"indicators.ma.periods 含非法值: {p}")

    macd = ind.get("macd", {})
    for param in ("fast", "slow", "signal"):
        val = macd.get(param)
        if val is None or not isinstance(val, int) or val <= 0:
            errors.append(f"indicators.macd.{param} 非法: {val}")
    if macd.get("fast", 0) >= macd.get("slow", 0):
        errors.append("indicators.macd.fast 应小于 slow")

    boll = ind.get("bollinger", {})
    if not isinstance(boll.get("period"), int) or boll.get("period", 0) <= 0:
        errors.append("indicators.bollinger.period 非法")
    if not isinstance(boll.get("num_std"), (int, float)) or boll.get("num_std", 0) <= 0:
        errors.append("indicators.bollinger.num_std 非法")

    # 3. spacetime
    st = config.get("spacetime", {})
    tol = st.get("time_symmetry_tolerance_weeks")
    if tol is not None and (not isinstance(tol, int) or tol < 0):
        errors.append(f"spacetime.time_symmetry_tolerance_weeks 非法: {tol}")
    tw = st.get("time_windows", [])
    if not isinstance(tw, list) or not tw:
        warnings.append("spacetime.time_windows 为空")

    # 4. state_machine
    sm = config.get("state_machine", {})
    states = sm.get("states", [])
    expected_states = {
        "EXTREME_STRONG", "STRONG", "MODERATE_STRONG",
        "MODERATE_WEAK", "WEAK", "EXTREME_WEAK",
    }
    if set(states) != expected_states:
        missing_states = expected_states - set(states)
        extra_states = set(states) - expected_states
        if missing_states:
            errors.append(f"state_machine.states 缺少: {missing_states}")
        if extra_states:
            warnings.append(f"state_machine.states 多余: {extra_states}")

    # 5. decision_matrix
    dm = config.get("decision_matrix", {})
    if not dm:
        errors.append("decision_matrix 为空")
    else:
        for state in expected_states:
            if state not in dm:
                errors.append(f"decision_matrix 缺少状态: {state}")
            else:
                state_entry = dm[state]
                for direction in ("UP", "DOWN"):
                    if direction not in state_entry:
                        errors.append(f"decision_matrix.{state} 缺少方向: {direction}")
                    else:
                        dir_entry = state_entry[direction]
                        if "action" not in dir_entry:
                            errors.append(f"decision_matrix.{state}.{direction} 缺少 action")
                        if "structures" not in dir_entry:
                            errors.append(f"decision_matrix.{state}.{direction} 缺少 structures")

    # 6. risk
    risk = config.get("risk", {})
    tiers = risk.get("position_tiers", [])
    if not tiers:
        warnings.append("risk.position_tiers 为空")
    else:
        for i, tier in enumerate(tiers):
            if "max_capital" not in tier or "max_single_position" not in tier:
                errors.append(f"risk.position_tiers[{i}] 缺少必要字段")
            max_pos = tier.get("max_single_position", 0)
            if not (0 < max_pos <= 1):
                errors.append(f"risk.position_tiers[{i}].max_single_position 应在 (0, 1]: {max_pos}")

    # 7. gateway
    gw = config.get("gateway", {})
    if not gw.get("default_source"):
        warnings.append("gateway.default_source 未设置")

    # 输出 warnings
    all_messages = errors + [f"[WARNING] {w}" for w in warnings]
    return len(errors) == 0, all_messages


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Trinity OS 配置校验")
    parser.add_argument("--config", default="config/macro_config.yaml", help="配置文件路径")
    args = parser.parse_args(argv)

    ok, messages = validate_config(args.config)

    print(f"配置文件: {args.config}")
    print(f"校验结果: {'✓ 通过' if ok else '✗ 失败'}")
    if messages:
        print(f"消息 ({len(messages)}):")
        for msg in messages:
            print(f"  {msg}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
