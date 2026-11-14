# -*- coding: utf-8 -*-
"""
run_daily_instruction.py — 每日轮动「快照 → 可执行指令」一键编排
==============================================================
把 L1 轮动信号门控需要的 QQQ/SPY 200MA 状态（quote_200ma.py）自动算出来，
再传给 daily_allocator.py 生成交易指令块。这是原 daily_allocator 手动调用
的自动化版本，供 9:20 快照自动化在写完 rotation_result md 后作为最后一步调用。

流程：
  1. quote_200ma.py 取 QQQ/SPY 现价 vs 200MA → 两个布尔（精确，非启发式）
  2. 自动定位今日（或最新且可解析的）rotation_result_*.md 与 sector_rotation_*.md
  3. 调用 daily_allocator.py --qqq-above-200ma/--spy-above-200ma
  4. 打印建仓门控状态摘要

用法：
  python run_daily_instruction.py                 # 自动发现今日 md
  python run_daily_instruction.py --rotation R.md --sector S.md   # 指定文件
  python run_daily_instruction.py --qqq-above-200ma true --spy-above-200ma false  # 跳过 quote

退出码：0 成功；1 找不到可解析的 md；2 quote_200ma 失败（仍回退启发式尽力生成）
"""
import os
import sys
import json
import glob
import importlib.util
import subprocess
import argparse
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable


def _load_daily_allocator():
    spec = importlib.util.spec_from_file_location(
        "daily_allocator", os.path.join(HERE, "daily_allocator.py"))
    da = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(da)
    return da


def discover_rotation(target_date):
    """优先今日 rotation_result，回退到任意最新且 daily_allocator 能解析的文件。"""
    da = _load_daily_allocator()
    patterns = [
        f"rotation_result_{target_date}_*.md",
        os.path.join("rotation_results", f"rotation_result_{target_date}_*.md"),
        os.path.join("output", "_archive", "**", "rotation_result_*.md"),
        "rotation_result_*.md",
        os.path.join("rotation_results", "rotation_result_*.md"),
    ]
    cands = []
    for p in patterns:
        cands.extend(glob.glob(os.path.join(HERE, p), recursive=True))
    cands = sorted(set(cands), key=lambda x: os.path.getmtime(x), reverse=True)
    for c in cands:
        try:
            da.parse_rotation_md(c)
            return c
        except Exception:
            continue
    return cands[0] if cands else None


def discover_sector():
    pats = [
        os.path.join("output", "sector_rotation_*.md"),
        os.path.join("output", "snapshot_*", "sector_rotation_*.md"),
        os.path.join("output", "_archive", "**", "sector_rotation_*.md"),
    ]
    cands = []
    for p in pats:
        cands.extend(glob.glob(os.path.join(HERE, p), recursive=True))
    if not cands:
        return None
    return max(cands, key=lambda x: os.path.getmtime(x))


def get_200ma(qqq_arg, spy_arg):
    """返回 (qqq_above, spy_above, ok, raw)。命令行已给定则直接用，否则调 quote_200ma。"""
    if qqq_arg is not None and spy_arg is not None:
        return qqq_arg, spy_arg, True, {"source": "cli"}
    try:
        out = subprocess.run([PY, os.path.join(HERE, "quote_200ma.py")],
                             capture_output=True, text=True, timeout=200, cwd=HERE)
        raw = json.loads(out.stdout.strip())
        ok = bool(raw.get("ok"))
        return raw.get("qqq_above_200ma"), raw.get("spy_above_200ma"), ok, raw
    except Exception as exc:
        return None, None, False, {"error": str(exc)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rotation", default=None, help="rotation_result md（默认自动发现今日）")
    ap.add_argument("--sector", default=None, help="sector_rotation md（默认自动发现最新）")
    ap.add_argument("--qqq-above-200ma", type=lambda x: x.lower() in ("1", "true", "y"), default=None)
    ap.add_argument("--spy-above-200ma", type=lambda x: x.lower() in ("1", "true", "y"), default=None)
    ap.add_argument("--out", default=None, help="合成指令输出路径")
    ap.add_argument("--profile", default="momentum",
                    choices=["momentum", "balanced", "aggressive", "defensive"],
                    help="兼容旧参数；默认落地 C+Trinity生产 / A+Trinity研究")
    ap.add_argument("--macro", default=None, help="daily_macro_*.json，默认 allocator 自动发现")
    ap.add_argument("--no-calibrated-selection", action="store_true")
    args = ap.parse_args()

    today = date.today().strftime("%Y-%m-%d")
    rotation = args.rotation or discover_rotation(today)
    sector = args.sector or discover_sector()

    if not rotation:
        print(f"[FAIL] 未找到可解析的 rotation_result md（日期 {today}）", file=sys.stderr)
        sys.exit(1)
    if not sector:
        print(f"[FAIL] 未找到 sector_rotation md", file=sys.stderr)
        sys.exit(1)

    qqq_above, spy_above, ok, raw = get_200ma(args.qqq_above_200ma, args.spy_above_200ma)

    cmd = [PY, os.path.join(HERE, "daily_allocator.py"),
           "--rotation", rotation, "--sector", sector]
    if qqq_above is not None:
        cmd += ["--qqq-above-200ma", "true" if qqq_above else "false"]
    if spy_above is not None:
        cmd += ["--spy-above-200ma", "true" if spy_above else "false"]
    if args.out:
        cmd += ["--out", args.out]
    cmd += ["--profile", args.profile]
    if args.macro:
        cmd += ["--macro", args.macro]
    if args.no_calibrated_selection:
        cmd += ["--no-calibrated-selection"]

    print(f"[INFO] rotation = {rotation}")
    print(f"[INFO] sector   = {sector}")
    print(f"[INFO] 200MA 来源 = {'CLI' if (args.qqq_above_200ma is not None) else ('quote_200ma' if ok else '⚠️ 失败→启发式ON')}")
    if ok and raw:
        print(f"[INFO] QQQ {raw.get('qqq_close')} vs SMA200 {raw.get('qqq_sma200')} → {'上方' if qqq_above else '下方'}")
        print(f"[INFO] SPY {raw.get('spy_close')} vs SMA200 {raw.get('spy_sma200')} → {'上方' if spy_above else '下方'}")

    r = subprocess.run(cmd, cwd=HERE)
    if r.returncode != 0:
        print(f"[FAIL] daily_allocator 返回 {r.returncode}", file=sys.stderr)
        sys.exit(r.returncode)
    print(f"[OK] 每日指令已生成（C+Trinity生产 / A+Trinity研究，profile={args.profile} 仅兼容）")

    # 纸面净值累计对比（A/B/C）
    abc_json = os.path.join(HERE, "rotation_results", f"daily_abc_{today}.json")
    instr = args.out or os.path.join(HERE, "rotation_results", f"daily_instruction_{today}.md")
    ledger_cmd = [PY, os.path.join(HERE, "paper_abc_ledger.py"),
                  "--append-to", instr,
                  "--append-to", rotation,
                  "--append-to", sector]
    if os.path.isfile(abc_json):
        # 全量快照重放（含今日）
        pass
    lr = subprocess.run(ledger_cmd, cwd=HERE)
    if lr.returncode != 0:
        print(f"[WARN] paper_abc_ledger 返回 {lr.returncode}", file=sys.stderr)
    else:
        print("[OK] 纸面净值累计对比表已更新")
    sys.exit(0)


if __name__ == "__main__":
    main()
