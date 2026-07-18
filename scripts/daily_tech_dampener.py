"""Daily Equity-Stress Overlay bridge (SOXX 20d drawdown -> kernel).

Bridges the daily denominator-state-machine automation's SOXX reading into the
macro kernel so the C-grade microstructural dampener fires in live trading.

What it does
------------
1. Computes SOXX trailing 20-day peak-to-trough drawdown (authoritative source:
   yfinance, file-cached in data/_tech_drawdown_sox.csv).
2. Cross-checks against the SOXX thermometer printed by the denominator state
   machine (资金价格斜率状态机 v1.6) in output/denominator_state_<date>.md.
3. Feeds ``tech_drawdown`` to ``core.decision_kernel.decide`` and emits the
   blended risk budget + dampener audit trail.

Output: output/tech_dampener_decision_<date>.md  +  output/tech_drawdown_<date>.json

Note: this is a *diagnostic/decision* bridge for the daily automation. The
authoritative production integration lives in runtime/orchestrator.py
(run_pipeline / dry_run), which injects the same tech_drawdown into the real
macro pipeline.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any, Dict, Optional

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("daily_tech_dampener")

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = REPO_ROOT / "output"

# When launched by the daily automation, the denominator-state report lives in
# the *tradingview* workspace output dir (cwd of the automation), not macro-os.
# --report-dir overrides both the cross-check read path and the output path so
# every artifact lands next to output/denominator_state_<date>.md.
DEFAULT_REPORT_DIR = OUTPUT_DIR


def compute_drawdown(days: int, lag: int, force_refresh: bool):
    """Return (raw_dd, smoothed_dd). Production injects the SMOOTHED value; the raw
    is kept only for cross-check transparency against the denominator thermometer.
    """
    sys.path.insert(0, str(REPO_ROOT))
    from adapters.equity_stress import compute_soxx_drawdown, compute_soxx_drawdown_smoothed

    raw = compute_soxx_drawdown(days=days, force_refresh=force_refresh)
    smoothed = compute_soxx_drawdown_smoothed(days=days, lag=lag, force_refresh=force_refresh)
    return raw, smoothed


def _extract_soxx_thermometer(md_path: Path) -> Optional[str]:
    """Best-effort: pull the SOXX line from the denominator state-machine report."""
    if not md_path.exists():
        return None
    try:
        text = md_path.read_text(encoding="utf-8")
    except Exception:
        return None
    # e.g. "最异常温度计: SOXX(Z5=-1.2强)" or "SOXX(Z5=-1.2)"
    m = re.search(r"SOXX\(Z5=[^)\s]+\)", text)
    if m:
        return m.group(0)
    m = re.search(r"SOXX[^)\n]{0,40}", text)
    return m.group(0).strip() if m else None


def run_decision(tech_dd: float, hard_regime: str = "RISK_ON") -> Dict[str, Any]:
    from core.decision_kernel import decide

    features: Dict[str, Any] = {"tech_drawdown": tech_dd}
    decision = decide(
        features=features,
        hard_regime=hard_regime,
        soft_regime_label="RISK_ON",
        risk_score=0.8,
        confidence=0.8,
        config={},
        proposed_risk=0.80,
        previous_risk_budget=0.80,  # assume continuity so velocity clamp does not mask the dampener
        days_in_recovery=0,
    )
    note = decision.audit_trail.get("step_2c_tech_dampener")
    return {
        "tech_drawdown": tech_dd,
        "hard_regime": hard_regime,
        "risk_budget": decision.risk_budget,
        "defense_budget": decision.defense_budget,
        "authority": decision.authority.value,
        "reason_code": decision.reason_code,
        "dampener_active": bool(note and note.get("active")),
        "dampener_note": note,
    }


def build_report(date_str: str, tech_dd: Optional[float], thermometer: Optional[str],
                 decision: Optional[Dict[str, Any]], raw_dd: Optional[float] = None) -> str:
    lines = [
        f"# 科技板块减震器内核决策 | {date_str}",
        "",
        f"- **SOXX 20日峰值回撤 (tech_drawdown, 迟滞带平滑值=喂内核)**: "
        f"{('-%.2f%%' % (abs(tech_dd) * 100)) if tech_dd is not None else '（数据不可用，减震器休眠）'}",
        f"- **SOXX 20日原始峰值回撤 (raw, 仅交叉校验)**: "
        f"{('-%.2f%%' % (abs(raw_dd) * 100)) if raw_dd is not None else '（不可用）'}",
        f"- **分母状态机 SOXX 温度计 (交叉校验)**: {thermometer or '（未读取到）'}",
        "",
    ]
    if tech_dd is None:
        lines += [
            "> ⚠️ SOXX 数据获取失败（网络/代理），`tech_drawdown` 缺省为 0.0，减震器未激活。",
            "> 内核决策维持宏观默认（本脚本不重算宏观，仅演示减震器）。",
            "",
        ]
        return "\n".join(lines)

    if decision:
        note = decision.get("dampener_note") or {}
        if decision["dampener_active"]:
            lines += [
                "## 🛡️ 减震器激活",
                "",
                f"- 原始预算（宏观允许）: **{note.get('pre_cap_budget')}**",
                f"- 结构封顶后预算: **{note.get('post_cap_budget')}** （cap={note.get('cap')}）",
                f"- 权限层级: `{decision['authority']}` | reason: `{decision['reason_code']}`",
                "",
                f"> SOXX 回撤越深，科技多头敞口被压得越低（C 档: ≥-13%→0.35 / ≥-10%→0.50 / ≥-7%→0.65）。",
            ]
        else:
            lines += [
                "## ✅ 减震器未触发",
                "",
                f"- 当前预算: **{decision['risk_budget']}** （SOXX 回撤未达 -7% 门槛，减震器放行）",
                f"- 权限层级: `{decision['authority']}` | reason: `{decision['reason_code']}`",
            ]
    lines += [
        "",
        "---",
        "*数据源: SOXX 真实日线（yfinance，文件缓存）。交叉校验: 资金价格斜率状态机 v1.6 的 SOXX 温度计。*",
        "*本文件为减震器演示/决策，非投资建议。*",
        "",
    ]
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    sys.path.insert(0, str(REPO_ROOT))
    parser = argparse.ArgumentParser(description="Daily SOXX drawdown -> kernel dampener bridge")
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="YYYY-MM-DD")
    parser.add_argument("--days", type=int, default=20, help="drawdown lookback window")
    parser.add_argument("--lag", type=int, default=2, help="hysteresis-band lag days (出档慢确认期, 经长周期 A/B 校准)")
    parser.add_argument("--hard-regime", default="RISK_ON", help="kernel hard_regime scenario")
    parser.add_argument("--force-refresh", action="store_true", help="ignore SOXX cache")
    parser.add_argument("--soxx-drawdown", type=float, default=None,
                        help="override drawdown (e.g. from TV MCP) instead of yfinance")
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR),
                        help="dir holding output/denominator_state_<date>.md and where outputs are written")
    args = parser.parse_args(argv)

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)

    # 1. authoritative drawdown (raw for transparency, smoothed for the kernel)
    if args.soxx_drawdown is not None:
        raw_dd = args.soxx_drawdown
        tech_dd = args.soxx_drawdown
        logger.info("Using override tech_drawdown=%.4f", tech_dd)
    else:
        raw_dd, tech_dd = compute_drawdown(args.days, args.lag, args.force_refresh)
        logger.info("Computed SOXX drawdown raw=%.4f smoothed=%.4f",
                    raw_dd if raw_dd is not None else float('nan'),
                    tech_dd if tech_dd is not None else float('nan'))

    # 2. cross-check vs denominator state machine report
    md_path = report_dir / f"denominator_state_{args.date}.md"
    thermometer = _extract_soxx_thermometer(md_path)

    # 3. feed kernel (uses the hysteresis-smoothed value — production parity)
    decision = run_decision(tech_dd, hard_regime=args.hard_regime) if tech_dd is not None else None

    # 4. emit
    report = build_report(args.date, tech_dd, thermometer, decision, raw_dd)
    out_md = report_dir / f"tech_dampener_decision_{args.date}.md"
    out_md.write_text(report, encoding="utf-8")

    payload = {
        "date": args.date,
        "tech_drawdown": tech_dd,
        "tech_drawdown_raw": raw_dd,
        "hysteresis_lag_days": args.lag,
        "soxx_thermometer": thermometer,
        "decision": decision,
    }
    out_json = report_dir / f"tech_drawdown_{args.date}.json"
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print(report)
    print(f"\n[written] {out_md}")
    print(f"[written] {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
