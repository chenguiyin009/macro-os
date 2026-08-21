#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily Macro Consolidated Report -- unify three daily observers.

Merges artifacts under output/:

  1) denominator state  -> denominator_state_<date>.{md,json}
  2) tech dampener      -> tech_drawdown_<date>.json
  3) theme state machine-> theme_state_machine_<date>.{md,json}
  4) NQ driver daily   -> nq_driver_<date>.{md,json}

Writes output/daily_macro_<date>.md + .json

Date contract (P0): prefer exact --date files; fallback only with --allow-stale
within configured lag; always surface as_of + warnings.

Observation only — not trade signals. Kernel decide() remains source of truth.

Usage:
    python scripts/daily_macro_consolidated.py --date 2026-07-20
    python scripts/daily_macro_consolidated.py --date 2026-07-20 --force-refresh
    python scripts/daily_macro_consolidated.py --date 2026-07-20 --allow-stale
    python scripts/daily_macro_consolidated.py --date 2026-07-20 --no-run-theme
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("daily-macro-consolidated")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = REPO_ROOT.parent / "output"
DEFAULT_CONFIG = REPO_ROOT / "config" / "daily_macro_consolidated.yaml"

THEME_SCRIPT = REPO_ROOT / "scripts" / "theme_state_machine_daily.py"
TECH_SCRIPT = REPO_ROOT / "scripts" / "daily_tech_dampener.py"
DENOM_SCRIPT = REPO_ROOT / "scripts" / "denominator_state_daily.py"
NQ_SCRIPT = REPO_ROOT / "scripts" / "nq_driver_daily.py"
SECTOR_SCRIPT = REPO_ROOT / "scripts" / "sector_rotation_daily.py"
SHOCK_SCRIPT = REPO_ROOT / "scripts" / "shock_absorption_daily.py"

_DATE_RE = re.compile(r"(20\d{2}-\d{2}-\d{2})")

_DEFAULT_CFG: Dict[str, Any] = {
    "stale_fallback_max_trading_days": 1,
    "denominator_ceilings": {
        "crisis": {
            "keywords": ["HARD_VETO", "CRISIS", "LIQUIDITY_SQUEEZE", "危机"],
            "ceiling": 0.10,
        },
        "tight": {
            "keywords": ["RISK_OFF", "TIGHT", "TRANSITION", "紧缩", "压力", "偏紧", "过渡"],
            "ceiling": 0.35,
        },
        "unconfirmed": {
            "keywords": ["UNCONFIRMED", "未确认", "分裂", "横盘", "混合"],
            "ceiling": 0.55,
        },
        "risk_on": {"keywords": ["RISK_ON", "宽松"], "ceiling": 0.80},
        "default": 0.55,
    },
    "theme_ceilings": {
        "missing": None,
        "pressure_override": 0.25,
        "risk_off": 0.45,
        "risk_on": 0.80,
        "default": 0.60,
    },
    "tech_default_ceiling": 0.80,
    # Frozen C-tier bands (authoritative SOXX path is tech dampener/kernel)
    "tech_stress_bands": {"strong": 0.35, "medium": 0.50, "light": 0.65},
    "tech_policy": {
        "mode": "frozen_c_tier",
        "note": "SOXX -13/-10/-7 -> 0.35/0.50/0.65; do not retune here",
    },
    # D-enhancement: denom bind + ceiling exit hysteresis
    "denom_policy": {
        "bind": True,
        "confirm_enter": 1,
        "confirm_exit": 3,
        "persist_hysteresis": True,
    },
    # Rates soft/diagnostic by default
    "rates_stress": {
        "enabled": True,
        "bind_mode": "shadow",
        "w_trade": 5,
        "w_vol": 60,
        "w_lvl": 756,
        "z_enter": 1.0,
        "pct_enter": 97.0,
        "confirm_days": 2,
        "exit_days": 3,
        "cap_level_only": 0.65,
        "cap_slope_level": 0.55,
        "cap_extreme": 0.45,
        "z_extreme": 1.75,
        "tlt_z_confirm": 0.0,
        "b_zone_cap": 0.65,
    },
    "positioning_path": {
        "enabled": True,
        "mode": "flag_only",
        "z_dead": 0.5,
        "z_enter": 1.0,
        "nq_break_ret_20": -0.05,
        "nq_lead_eps": 0.0,
        "require_lh_ll_for_deteriorate": True,
        "swing_left": 2,
        "swing_right": 2,
        "swing_lookback": 60,
        "soft_cap_deteriorate": 0.55,
        "soft_cap_bad_ease": 0.45,
        "confirm_days": 2,
        "exit_days": 2,
        "deteriorate_confirm_days": 3,
    },
    # Scheme A: dual budget re-risk (defense ceiling + stepped target)
    "re_risk": {
        "enabled": True,
        "offense_cap": 0.80,
        "max_step_up": 0.10,
        "step_confirm_days": 3,
        "min_hold_after_up_days": 2,
        "tech_min_for_permit": 0.50,
        "require_not_lh_ll": True,
        "require_ret20_nonneg": True,
        "block_both_tight": True,
        "block_ppo_block_add": True,
        "permit_tiers": ["unconfirmed", "risk_on", "default"],
    },
}


def load_config(path: Optional[Path] = None) -> Dict[str, Any]:
    cfg: Dict[str, Any] = json.loads(json.dumps(_DEFAULT_CFG))
    p = path or DEFAULT_CONFIG
    if not p.exists():
        return cfg
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            return cfg
        for k, v in loaded.items():
            if k in ("denominator_ceilings", "theme_ceilings", "tech_stress_bands", "rates_stress", "denom_policy", "tech_policy", "positioning_path", "re_risk") and isinstance(v, dict):
                base = cfg.get(k) or {}
                if isinstance(base, dict):
                    base.update(v)
                    cfg[k] = base
                else:
                    cfg[k] = v
            else:
                cfg[k] = v
    except Exception as exc:  # noqa: BLE001
        logger.warning("config load failed (%s): %s; using defaults", p, exc)
    return cfg


def _run_script(script: Path, args: list, errors: Optional[List[str]] = None) -> bool:
    cmd = [sys.executable, str(script)] + args
    logger.info("subprocess: %s", " ".join(cmd))
    env = dict(**__import__("os").environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=300,
            env=env,
        )
    except subprocess.TimeoutExpired:
        msg = f"subprocess timed out: {script.name}"
        logger.warning(msg)
        if errors is not None:
            errors.append(msg)
        return False
    except Exception as exc:  # noqa: BLE001
        msg = f"subprocess failed: {script.name} -> {exc}"
        logger.warning(msg)
        if errors is not None:
            errors.append(msg)
        return False
    if proc.returncode != 0:
        tail = (proc.stderr or "").strip().splitlines()[-1] if proc.stderr else ""
        msg = f"{script.name} exited {proc.returncode}: {tail}"
        logger.warning(msg)
        if errors is not None:
            errors.append(msg)
        return False
    return True


def _date_from_name(path: Path) -> Optional[str]:
    m = _DATE_RE.search(path.name)
    return m.group(1) if m else None


def _newest_dated(report_dir: Path, pattern: str) -> Optional[Path]:
    hits = [p for p in report_dir.glob(pattern) if _date_from_name(p)]
    if not hits:
        return None

    def _key(p: Path):
        return (_date_from_name(p) or "", p.stat().st_mtime)

    return sorted(hits, key=_key, reverse=True)[0]


def _newest(report_dir: Path, pattern: str) -> Optional[Path]:
    """Backward-compatible alias for _newest_dated.

    旧版脚本与测试依赖 dmc._newest；重构后内部统一用 _newest_dated
    （按文件名日期排序、mtime 仅作 tie-breaker，二者语义一致）。
    保留此别名避免 AttributeError，且不重复实现。
    """
    return _newest_dated(report_dir, pattern)


def resolve_artifact(
    report_dir: Path,
    exact_name: str,
    glob_pattern: str,
    expected_date: str,
    *,
    allow_stale: bool,
    max_lag_days: int,
    kind: str,
    warnings: List[str],
) -> Tuple[Optional[Path], bool]:
    exact = report_dir / exact_name
    if exact.exists():
        return exact, False

    newest = _newest_dated(report_dir, glob_pattern)
    if newest is None:
        return None, False

    file_date = _date_from_name(newest) or ""
    lag = _busday_lag(file_date, expected_date)
    if not allow_stale:
        msg = (
            f"{kind}: missing {exact_name}; newest is {newest.name} "
            f"(as_of={file_date}). Pass --allow-stale to use fallback "
            f"(max_lag={max_lag_days} trading days)."
        )
        logger.warning(msg)
        warnings.append(msg)
        return None, False

    if lag is None or lag > max_lag_days:
        msg = (
            f"{kind}: fallback {newest.name} lag={lag} trading days exceeds "
            f"max_lag={max_lag_days} trading days vs expected {expected_date}; refusing."
        )
        logger.warning(msg)
        warnings.append(msg)
        return None, False

    msg = (
        f"{kind}: using stale fallback {newest.name} (as_of={file_date}, "
        f"expected={expected_date}, lag_td={lag})"
    )
    logger.warning(msg)
    warnings.append(msg)
    return newest, True


def _normalize_denom(d: Dict[str, Any], date_str: str) -> Dict[str, Any]:
    state = (d.get("state") or d.get("main_state") or "").replace(" ", "")
    return {
        "date": d.get("date") or date_str,
        "main_state": state,
        "state": state,
        "raw_state": (d.get("raw_state") or state or "").replace(" ", ""),
        "confidence_label": d.get("confidence_label", ""),
        "four_quad": d.get("quadrant") or d.get("four_quad") or "",
        "dont_do": d.get("dont_do", ""),
        "trigger_hint": d.get("trigger_hint", ""),
        "z5": d.get("z5", {}),
        "source": d.get("source") or "json",
    }


def _parse_denom_md(md: Path, date_str: str) -> Dict[str, Any]:
    text = md.read_text(encoding="utf-8")

    def _field(label: str) -> str:
        # table format: | label | value |
        m = re.search(rf"\|\s*{re.escape(label)}\s*\|\s*(.+?)\s*\|", text)
        if m:
            val = m.group(1).strip()
        else:
            # bullet format: - **label**：value  (TV CDP 盘前自动化产出)
            m2 = re.search(rf"-\s*\*{{1,2}}{re.escape(label)}\*{{1,2}}\s*[：:]\s*(.+?)(?:\n|$)", text)
            if not m2:
                return ""
            val = m2.group(1).strip()
        val = re.sub(r"\*\*(.+?)\*\*", r"\1", val)
        return val.replace("［", "[").replace("］", "]")

    mdate = _DATE_RE.search(md.name)
    asof = mdate.group(1) if mdate else date_str
    ms = _field("主状态").replace(" ", "")
    return {
        "date": asof,
        "main_state": ms,
        "state": ms,
        "raw_state": ms,
        "confidence_label": "",
        "four_quad": _field("四象限（官方）"),
        "dont_do": _field("今天不做什么"),
        "trigger_hint": _field("触发提示"),
        "z5": {},
        "source": "md",
    }


def load_denominator(
    report_dir: Path,
    date_str: str,
    *,
    allow_stale: bool,
    max_lag_days: int,
    warnings: List[str],
    errors: List[str],
) -> Optional[Dict[str, Any]]:
    path, _fb = resolve_artifact(
        report_dir,
        f"denominator_state_{date_str}.json",
        "denominator_state_*.json",
        date_str,
        allow_stale=allow_stale,
        max_lag_days=max_lag_days,
        kind="denominator",
        warnings=warnings,
    )
    if path is not None:
        try:
            d = json.loads(path.read_text(encoding="utf-8"))
            return _normalize_denom(d, _date_from_name(path) or date_str)
        except Exception as exc:  # noqa: BLE001
            msg = f"denominator json parse failed ({path.name}): {exc}"
            logger.warning(msg)
            errors.append(msg)

    path_md, _fb2 = resolve_artifact(
        report_dir,
        f"denominator_state_{date_str}.md",
        "denominator_state_*.md",
        date_str,
        allow_stale=allow_stale,
        max_lag_days=max_lag_days,
        kind="denominator-md",
        warnings=warnings,
    )
    if path_md is not None and "analysis" not in path_md.name:
        try:
            return _parse_denom_md(path_md, date_str)
        except Exception as exc:  # noqa: BLE001
            msg = f"denominator md parse failed ({path_md.name}): {exc}"
            logger.warning(msg)
            errors.append(msg)
    return None


def ensure_denominator(
    report_dir: Path,
    date_str: str,
    no_run: bool,
    *,
    allow_stale: bool,
    max_lag_days: int,
    warnings: List[str],
    errors: List[str],
) -> Optional[Dict[str, Any]]:
    if not no_run and DENOM_SCRIPT.exists():
        _run_script(
            DENOM_SCRIPT,
            ["--date", date_str, "--report-dir", str(report_dir)],
            errors,
        )
    elif not no_run:
        warnings.append("denominator script missing; load existing artifact only")
    # FRED/headless port writes as_of last available market date (often T-1).
    # Prefer exact; if missing after run, allow configured lag without requiring CLI --allow-stale.
    loaded = load_denominator(
        report_dir,
        date_str,
        allow_stale=allow_stale,
        max_lag_days=max_lag_days,
        warnings=warnings,
        errors=errors,
    )
    if loaded is None and not allow_stale:
        soft_warnings: List[str] = []
        loaded = load_denominator(
            report_dir,
            date_str,
            allow_stale=True,
            max_lag_days=max_lag_days,
            warnings=soft_warnings,
            errors=errors,
        )
        if loaded is not None:
            msg = (
                f"denominator: exact {date_str} missing; using last-available "
                f"{loaded.get('date')} within lag={max_lag_days}d (FRED last-print semantics)"
            )
            logger.warning(msg)
            warnings.append(msg)
            warnings.extend(soft_warnings)
    return loaded


def ensure_tech(
    report_dir: Path,
    date_str: str,
    no_run: bool,
    *,
    warnings: List[str],
    errors: List[str],
) -> Optional[Dict[str, Any]]:
    target = report_dir / f"tech_drawdown_{date_str}.json"
    if no_run:
        if not target.exists():
            msg = "tech json missing and --no-run-tech set; skipping"
            logger.warning(msg)
            warnings.append(msg)
            return None
        try:
            return json.loads(target.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            msg = f"tech json parse failed: {exc}"
            logger.warning(msg)
            errors.append(msg)
            return None
    ok = _run_script(TECH_SCRIPT, ["--date", date_str, "--report-dir", str(report_dir)], errors)
    if target.exists():
        try:
            payload = json.loads(target.read_text(encoding="utf-8"))
            if not ok:
                warnings.append(
                    f"tech dampener exited non-zero but artifact present ({target.name}); loaded anyway"
                )
            return payload
        except Exception as exc:  # noqa: BLE001
            msg = f"tech json parse failed after run: {exc}"
            logger.warning(msg)
            errors.append(msg)
            return None
    msg = "tech dampener unavailable"
    logger.warning(msg)
    warnings.append(msg)
    return None


def ensure_theme(
    report_dir: Path,
    date_str: str,
    no_run: bool,
    *,
    allow_stale: bool,
    max_lag_days: int,
    warnings: List[str],
    errors: List[str],
) -> Optional[Dict[str, Any]]:
    exact = report_dir / f"theme_state_machine_{date_str}.json"

    def _load(path: Path) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            msg = f"theme json parse failed ({path.name}): {exc}"
            logger.warning(msg)
            errors.append(msg)
            return None

    def _resolve() -> Optional[Path]:
        path, _fb = resolve_artifact(
            report_dir,
            exact.name,
            "theme_state_machine_*.json",
            date_str,
            allow_stale=allow_stale,
            max_lag_days=max_lag_days,
            kind="theme",
            warnings=warnings,
        )
        return path

    if no_run:
        path = _resolve()
        return _load(path) if path else None

    args = ["--report-dir", str(report_dir), "--date", date_str, "--expected-as-of", date_str]
    if _run_script(THEME_SCRIPT, args, errors) and exact.exists():
        return _load(exact)

    # Prefer exact; if missing after run, allow configured lag without requiring CLI --allow-stale.
    path = _resolve()
    if path is not None:
        return _load(path)
    if not allow_stale:
        soft_warnings: List[str] = []
        soft_path, _fb = resolve_artifact(
            report_dir,
            exact.name,
            "theme_state_machine_*.json",
            date_str,
            allow_stale=True,
            max_lag_days=max_lag_days,
            kind="theme",
            warnings=soft_warnings,
        )
        if soft_path is not None:
            warnings.extend(soft_warnings)
            return _load(soft_path)

    msg = "theme state machine unavailable"
    logger.warning(msg)
    warnings.append(msg)
    return None



def ensure_nq(
    report_dir: Path,
    date_str: str,
    no_run: bool,
    *,
    allow_stale: bool,
    max_lag_days: int,
    warnings: List[str],
    errors: List[str],
) -> Optional[Dict[str, Any]]:
    """Load/run NQ driver daily artifact (fourth observer leg)."""
    exact = report_dir / f"nq_driver_{date_str}.json"

    def _load(path: Path) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            msg = f"nq driver json parse failed ({path.name}): {exc}"
            logger.warning(msg)
            errors.append(msg)
            return None

    if no_run:
        path, _fb = resolve_artifact(
            report_dir,
            exact.name,
            "nq_driver_*.json",
            date_str,
            allow_stale=allow_stale,
            max_lag_days=max_lag_days,
            kind="nq_driver",
            warnings=warnings,
        )
        return _load(path) if path else None

    if NQ_SCRIPT.exists():
        _run_script(
            NQ_SCRIPT,
            ["--date", date_str, "--report-dir", str(report_dir)],
            errors,
        )
    else:
        warnings.append("nq_driver script missing; load existing artifact only")

    if exact.exists():
        return _load(exact)
    path, _fb = resolve_artifact(
        report_dir,
        exact.name,
        "nq_driver_*.json",
        date_str,
        allow_stale=allow_stale,
        max_lag_days=max_lag_days,
        kind="nq_driver",
        warnings=warnings,
    )
    if path is None:
        msg = "nq driver unavailable"
        logger.warning(msg)
        warnings.append(msg)
        return None
    return _load(path)


def ensure_sector(
    report_dir: Path,
    date_str: str,
    no_run: bool,
    *,
    allow_stale: bool,
    max_lag_days: int,
    warnings: List[str],
    errors: List[str],
) -> Optional[Dict[str, Any]]:
    """Load/run 标普板块资金轮动复刻 (Pine v1.3m) artifact.

    该读数锚定标普最新可用交易日（盘后跑即上一美国交易日），文件名按 as_of 计，
    与 report 日历日可能差 1 个交易日；故缺失时走 allow_stale 兜底（按交易日滞后）。
    板块轮动是美股比价视角的**互补观测**，不参与合成预算 min。
    """
    exact = report_dir / f"sector_rotation_{date_str}.json"

    def _load(path: Path) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            msg = f"sector rotation json parse failed ({path.name}): {exc}"
            logger.warning(msg)
            errors.append(msg)
            return None

    def _resolve(soft: bool) -> Optional[Path]:
        p, _fb = resolve_artifact(
            report_dir,
            exact.name,
            "sector_rotation_*.json",
            date_str,
            allow_stale=soft,
            max_lag_days=max_lag_days,
            kind="sector_rotation",
            warnings=warnings,
        )
        return p

    if no_run:
        path = _resolve(allow_stale)
        if path:
            return _load(path)
    else:
        if SECTOR_SCRIPT.exists():
            _run_script(
                SECTOR_SCRIPT,
                ["--report-dir", str(report_dir)],
                errors,
            )
        else:
            warnings.append("sector_rotation script missing; load existing artifact only")
        if exact.exists():
            return _load(exact)
        path = _resolve(allow_stale)
        if path:
            return _load(path)

    # 板块轮动锚定最新美国交易日，文件名日期常与 report 日历日差 <=1 交易日；
    # 即便全局未开 allow_stale，也以 allow_stale=True 作软兜底（仅记录警告，不阻断）。
    soft_path = _resolve(True)
    if soft_path is not None:
        return _load(soft_path)

    msg = "sector rotation unavailable"
    logger.warning(msg)
    warnings.append(msg)
    return None


def ensure_shock(
    report_dir: Path,
    date_str: str,
    no_run: bool,
    *,
    allow_stale: bool,
    max_lag_days: int,
    warnings: List[str],
    errors: List[str],
) -> Optional[Dict[str, Any]]:
    """Load/run 市场冲击消化能力评判 日频端口 artifact (denominator 旁证交叉校验).

    该读数定位为分母压力的**旁证**——只观察+报警，不进主状态机，不参与合成预算 min。
    文件名按数据对齐后的最新可得日计，常与 report 日历日差 <=1 交易日，故缺失时走
    allow_stale 软兜底（记录警告，不阻断），与板块轮动同策略。
    """
    exact = report_dir / f"shock_absorption_{date_str}.json"

    def _load(path: Path) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"shock absorption json parse failed ({path.name}): {exc}")
            return None

    def _resolve(soft: bool) -> Optional[Path]:
        p, _fb = resolve_artifact(
            report_dir,
            exact.name,
            "shock_absorption_*.json",
            date_str,
            allow_stale=soft,
            max_lag_days=max_lag_days,
            kind="shock_absorption",
            warnings=warnings,
        )
        return p

    if no_run:
        path = _resolve(allow_stale)
        if path:
            return _load(path)
    else:
        if SHOCK_SCRIPT.exists():
            _run_script(
                SHOCK_SCRIPT,
                ["--report-dir", str(report_dir)],
                errors,
            )
        else:
            warnings.append("shock_absorption script missing; load existing artifact only")
        if exact.exists():
            return _load(exact)
        path = _resolve(allow_stale)
        if path:
            return _load(path)

    soft_path = _resolve(True)
    if soft_path is not None:
        return _load(soft_path)

    msg = "shock absorption unavailable"
    logger.warning(msg)
    warnings.append(msg)
    return None


def _busday_lag(as_of: str, expected: str) -> Optional[int]:
    try:
        t_day = dt.date.fromisoformat(str(as_of)[:10])
        e_day = dt.date.fromisoformat(str(expected)[:10])
    except Exception:
        return None
    if e_day < t_day:
        t_day, e_day = e_day, t_day
    try:
        import numpy as np

        return int(np.busday_count(t_day, e_day))
    except Exception:
        return max(0, (e_day - t_day).days)


def _infer_theme_quality(theme: Dict[str, Any], theme_date: str, report_date: str) -> Dict[str, Any]:
    tq = dict(theme.get("quality") or {})
    if tq:
        return tq
    as_of = str(theme.get("date") or theme_date)[:10]
    lag = _busday_lag(as_of, report_date)
    if lag is None:
        return {}
    stale = lag > 1
    return {
        "as_of": as_of,
        "expected_as_of": str(report_date)[:10],
        "lag_days": lag,
        "stale": stale,
        "degraded": False,
        "data_quality": "stale" if stale else "ok",
        "inferred": True,
    }


def _classify_denom(main_state: str) -> Tuple[str, str]:
    s_compact = (main_state or "").replace(" ", "")
    if any(k in s_compact for k in ["未确认", "分裂", "横盘", "混合", "UNCONFIRMED"]):
        return "未确认/横盘", "none"
    if any(k in s_compact for k in ["RISK_OFF", "risk_off", "紧缩", "压力", "HARD_VETO", "CRISIS"]):
        return "偏紧/压力", "risk_off"
    if any(k in s_compact for k in ["RISK_ON", "risk_on", "宽松"]):
        return "偏松/确认", "risk_on"
    if "确认" in s_compact and "未确认" not in s_compact:
        return "偏松/确认", "risk_on"
    return "未确认/横盘", "none"


def _tech_stress(budget: Optional[float], bands: Dict[str, float]) -> str:
    if budget is None:
        return "未知"
    if budget <= float(bands.get("strong", 0.35)):
        return "强压制"
    if budget <= float(bands.get("medium", 0.50)):
        return "中度压制"
    if budget <= float(bands.get("light", 0.65)):
        return "轻度压制"
    return "无压制"


def _tech_bias_from_stress(stress: str) -> str:
    if stress in ("强压制", "中度压制"):
        return "risk_off"
    if stress == "轻度压制":
        return "mixed"
    if stress == "未知":
        return "none"
    return "risk_on"


def synthesize(
    theme: Optional[Dict],
    tech: Optional[Dict],
    denom: Optional[Dict],
    theme_date: str,
    tech_date: str,
    denom_date: Optional[str],
    report_date: Optional[str] = None,
    cfg: Optional[Dict[str, Any]] = None,
    nq: Optional[Dict] = None,
    nq_date: Optional[str] = None,
    shock: Optional[Dict] = None,
) -> Dict[str, Any]:
    cfg = cfg or _DEFAULT_CFG
    bands = cfg.get("tech_stress_bands") or _DEFAULT_CFG["tech_stress_bands"]
    report_date = report_date or tech_date or theme_date

    dec = (tech or {}).get("decision") or {}
    budget = dec.get("risk_budget")
    damp_active = dec.get("dampener_active")
    stress = _tech_stress(None if budget is None else float(budget), bands)

    if denom:
        main_state = denom.get("main_state", "") or ""
        dont = denom.get("dont_do", "")
        trigger = denom.get("trigger_hint", "")
        denom_tag, denom_bias = _classify_denom(main_state)
    else:
        main_state = dont = trigger = ""
        denom_tag, denom_bias = "未读取", "none"

    dom = (theme or {}).get("dominant_theme")
    leaders = (theme or {}).get("today_leaders") or []
    strength = (theme or {}).get("strength_table") or []
    tq = _infer_theme_quality(theme or {}, theme_date, report_date) if theme else {}

    theme_bias = (dom or {}).get("risk_bias") or (theme or {}).get("risk_bias") or "none"
    pressure = bool((dom or {}).get("pressure_override") or (theme or {}).get("pressure_override"))
    narrative = (
        f"有主导叙事：{dom.get('name')}（{dom.get('family')}，{theme_bias}）"
        if dom
        else "无主导叙事（等待共振）"
    )

    stale = bool(tq.get("stale"))
    degraded = bool(tq.get("degraded"))
    data_quality = tq.get("data_quality") or (
        "stale_degraded"
        if stale and degraded
        else "stale"
        if stale
        else "degraded"
        if degraded
        else "ok"
        if theme
        else "missing"
    )

    eq_down = any(l.get("dir") == "↓" and abs(float(l.get("z") or 0)) >= 1.0 for l in leaders)
    eq_down = eq_down or any(
        ("标普" in (s.get("label") or "") or "纳指" in (s.get("label") or ""))
        and s.get("dir") == "↓"
        and s.get("level") in ("强", "异常")
        for s in strength
    )
    tech_bias = _tech_bias_from_stress(stress)

    nq_driver = (nq or {}).get("driver") or {}
    nq_bias = (
        nq_driver.get("risk_bias")
        or (nq or {}).get("risk_bias")
        or "none"
    )
    nq_veto = bool(nq_driver.get("veto") or (nq or {}).get("pressure_override"))
    nq_q = ((nq or {}).get("quality") or {}).get("data_quality") or ("ok" if nq else "missing")

    if pressure or theme_bias == "risk_off" or nq_veto or nq_bias == "risk_off":
        bias = "risk_off"
    elif theme_bias == "risk_on" and tech_bias == "risk_on" and denom_bias != "risk_off" and nq_bias != "risk_off" and not nq_veto:
        bias = "risk_on"
    elif eq_down or tech_bias == "risk_off" or denom_bias == "risk_off":
        bias = "risk_off" if (eq_down and tech_bias == "risk_off") else "mixed"
    elif theme_bias == "mixed" or tech_bias == "mixed":
        bias = "mixed"
    else:
        bias = "none"

    if data_quality in ("stale", "degraded", "stale_degraded", "missing"):
        alignment = "数据降级"
    elif denom_tag == "未确认/横盘" and not dom and stress not in ("无压制",):
        alignment = "未确认"
    elif denom_tag == "未读取" and not dom:
        alignment = "未确认"
    else:
        nq_vote = nq_bias if nq and nq_q not in ("missing", "bad", "degraded") else None
        votes = [b for b in (denom_bias, tech_bias, theme_bias, nq_vote) if b in ("risk_on", "risk_off")]
        if (pressure and tech_bias == "risk_off") or (nq_veto and tech_bias == "risk_off"):
            alignment = "共振"
        elif len(votes) >= 2 and len(set(votes)) == 1:
            alignment = "共振"
        elif len(votes) >= 2 and len(set(votes)) > 1:
            alignment = "冲突"
        elif dom and stress != "未知":
            alignment = "部分"
        else:
            alignment = "未确认"

    divergences: List[str] = []
    if data_quality in ("stale", "stale_degraded"):
        divergences.append(
            f"主题数据陈旧/降级（as_of={tq.get('as_of')}, lag={tq.get('lag_days')}）—不得解读为跨工具共振"
        )
    if data_quality == "missing":
        divergences.append("主题状态机缺失")
    if not tech:
        divergences.append("科技减震器缺失")
    if not denom:
        divergences.append("分母状态缺失")
    if denom_bias == "risk_on" and tech_bias == "risk_off":
        divergences.append("分母偏松但减震器压制 — 交叉冲突")
    if denom_bias == "risk_off" and theme_bias == "risk_on" and not pressure:
        divergences.append("分母偏紧但主题 risk_on — 交叉冲突")
    if eq_down and stress == "无压制":
        divergences.append("股指强势下行但减震器未触发 — 留意滞后")
    if pressure and tech_bias == "risk_on":
        divergences.append("主题压力置顶但减震器未压制")
    if nq_driver.get("veto"):
        divergences.append(
            f"NQ动因硬否决: {nq_driver.get('state_name', '?')} — {nq_driver.get('veto_reason', nq_driver.get('driver_mod', ''))}"
        )
    elif (nq or {}).get("pressure_override"):
        divergences.append(
            f"NQ动因压力覆盖: {nq_driver.get('state_name', '?')} — {nq_driver.get('veto_reason', nq_driver.get('driver_mod', ''))}"
        )
    if nq and theme_bias == "risk_on" and nq_bias == "risk_off":
        divergences.append("主题偏多但 NQ 动因偏空 — 交叉冲突")
    if not nq:
        divergences.append("NQ 动因缺失")

    # --- 冲击吸收旁证交叉校验（分母压力视角，不进合成预算） ---
    shock_cross = None
    if shock:
        s_frag = int(shock.get("fragility_level", 0) or 0)
        s_state = shock.get("fragility_state", "—")
        shock_cross = {
            "fragility_level": s_frag,
            "fragility_state": s_state,
            "weak_link": shock.get("weak_link"),
            "shock_score": shock.get("shock_score"),
            "agree": None,
        }
        if s_frag == 2 and denom_bias != "risk_off":
            divergences.append(
                f"冲击吸收判【高度脆弱】但分母未收紧(risk_off)——分母旁证冲突："
                f"冲击或集中在分母未覆盖的腿（信用/波动/美元），分母可能滞后"
            )
            shock_cross["agree"] = False
        elif s_frag >= 1 and denom_bias == "risk_on":
            divergences.append(
                f"冲击吸收处于【{s_state}】但分母偏松(risk_on)——分母旁证冲突："
                f"分母宏观面宽松与微观冲击吸收恶化并存，提防分母滞后"
            )
            shock_cross["agree"] = False
        elif s_frag == 0 and denom_bias == "risk_off":
            divergences.append(
                f"冲击吸收【吸收正常】但分母偏紧(risk_off)——分母更前瞻或覆盖不同维度，非硬冲突"
            )
            shock_cross["agree"] = True
        elif (s_frag >= 1 and denom_bias == "risk_off") or (s_frag == 0 and denom_bias in ("none", "risk_on")):
            shock_cross["agree"] = True

    if alignment == "数据降级":
        consistency, tone = "数据降级（方向未确认）", "数据不完整或主题陈旧：只陈述分工具读数，不合成一致叙事。"
    elif alignment == "共振":
        consistency = "方向共振（观察层）"
        tone = (
            "观察层偏防守：跨工具偏向 risk_off 或压力置顶。"
            if bias == "risk_off"
            else "观察层偏多但非交易指令：仍以 kernel decide() 为准。"
        )
    elif alignment == "冲突":
        consistency, tone = "工具冲突", "工具互相打架：降杠杆叙事优先，等待下一交易日对齐。"
    elif alignment == "部分":
        consistency, tone = "部分对齐", "信息部分对齐：保持观察，不外推。"
    else:
        consistency, tone = "未确认/信息不足", "信息不足或未确认：保持观察，不外推。"

    return {
        "consistency": consistency,
        "alignment": alignment,
        "bias": bias,
        "data_quality": data_quality,
        "pressure_override": pressure,
        "equity_down": eq_down,
        "equity_stress": stress,
        "risk_budget": budget,
        "dampener_active": damp_active,
        "denom_tag": denom_tag,
        "denom_bias": denom_bias,
        "tech_bias": tech_bias,
        "theme_bias": theme_bias,
        "narrative": narrative,
        "divergences": divergences,
        "tone": tone,
        "theme_quality": tq,
        "dont_do": dont,
        "trigger_hint": trigger,
        "main_state": main_state,
        "theme_date": theme_date,
        "tech_date": tech_date,
        "denom_date": denom_date,
        "nq_date": nq_date,
        "nq_bias": nq_bias if nq else "none",
        "nq_veto": bool(nq_driver.get("veto")) if nq else False,
        "nq_pressure_override": bool((nq or {}).get("pressure_override")) if nq else False,
        "nq_state": (nq_driver.get("state_name") if nq else None),
        "nq_quality": nq_q,
        "nq_dont_do": (nq_driver.get("dont_do") or (nq or {}).get("dont_do") if nq else ""),
        "nq_driver_mod": (nq_driver.get("driver_mod") if nq else None),
        "shock_absorption": shock_cross,
    }


def _denom_ceiling(state: Optional[str], cfg: Optional[Dict[str, Any]] = None) -> float:
    """Raw denom ceiling from state labels (no hysteresis)."""
    cfg = cfg or _DEFAULT_CFG
    block = cfg.get("denominator_ceilings") or _DEFAULT_CFG["denominator_ceilings"]
    try:
        from core.denom_ceiling import classify_denom_tier, tier_to_ceiling

        policy = cfg.get("denom_policy") or {}
        tier = classify_denom_tier(state, block, policy.get("state_tier_map"))
        return float(tier_to_ceiling(tier, block))
    except Exception:
        s = (state or "").replace(" ", "")
        s_upper = s.upper()
        for tier in ("crisis", "unconfirmed", "tight", "risk_on"):
            spec = block.get(tier) or {}
            for kw in spec.get("keywords") or []:
                if not kw:
                    continue
                if kw.isascii():
                    if kw.upper() in s_upper:
                        return float(spec.get("ceiling", block.get("default", 0.55)))
                elif kw in s:
                    return float(spec.get("ceiling", block.get("default", 0.55)))
        if "确认" in s and "未确认" not in s:
            return float((block.get("risk_on") or {}).get("ceiling", 0.80))
        return float(block.get("default", 0.55))


def _load_prev_combined(report_dir: Path, date_str: str) -> Optional[Dict[str, Any]]:
    """Prior combined_risk_budget for denom ceiling exit hysteresis."""
    try:
        d0 = dt.date.fromisoformat(date_str)
    except Exception:
        return None
    for i in range(1, 10):
        d = (d0 - dt.timedelta(days=i)).isoformat()
        fp = report_dir / f"daily_macro_{d}.json"
        if not fp.exists():
            continue
        try:
            payload = json.loads(fp.read_text(encoding="utf-8"))
        except Exception:
            continue
        comb = payload.get("combined_risk_budget")
        if isinstance(comb, dict) and (
            comb.get("denominator_ceiling") is not None
            or comb.get("denominator_ceiling_held") is not None
        ):
            return comb
    return None


def bind_denom_ceiling_day(
    denom: Optional[Dict],
    *,
    cfg: Optional[Dict[str, Any]] = None,
    prev_combined: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """D-enhancement: state -> ceiling with enter/exit hysteresis."""
    cfg = cfg or _DEFAULT_CFG
    block = cfg.get("denominator_ceilings") or _DEFAULT_CFG["denominator_ceilings"]
    policy = dict(cfg.get("denom_policy") or _DEFAULT_CFG.get("denom_policy") or {})
    state = None
    if denom:
        state = denom.get("main_state") or denom.get("state")
    try:
        from core.denom_ceiling import bind_denom_ceiling
    except Exception as exc:  # noqa: BLE001
        raw = _denom_ceiling(state, cfg)
        return {
            "tier": "legacy",
            "raw_ceiling": raw,
            "ceiling": raw,
            "hysteresis": f"import_failed:{exc}",
            "bound": bool(policy.get("bind", True)),
            "state": state,
            "tight_streak": 0,
            "loose_streak": 0,
        }
    prev_c = None
    ts = 0
    ls = 0
    if policy.get("persist_hysteresis", True) and isinstance(prev_combined, dict):
        prev_c = prev_combined.get("denominator_ceiling_held")
        if prev_c is None:
            prev_c = prev_combined.get("denominator_ceiling")
        ts = int(prev_combined.get("denom_tight_streak") or 0)
        ls = int(prev_combined.get("denom_loose_streak") or 0)
    out = bind_denom_ceiling(
        state,
        cfg_block=block,
        hyst_cfg=policy,
        prev_ceiling=float(prev_c) if prev_c is not None else None,
        loose_streak=ls,
        tight_streak=ts,
    )
    out["bound"] = bool(policy.get("bind", True))
    out["state"] = state
    return out


def _theme_ceiling(theme: Optional[Dict], cfg: Optional[Dict[str, Any]] = None) -> Optional[float]:
    cfg = cfg or _DEFAULT_CFG
    tc = cfg.get("theme_ceilings") or _DEFAULT_CFG["theme_ceilings"]
    if not theme:
        return tc.get("missing", None)
    if theme.get("pressure_override") or ((theme.get("dominant_theme") or {}).get("pressure_override")):
        return float(tc.get("pressure_override", 0.25))
    rb = ((theme.get("dominant_theme") or {}).get("risk_bias") or theme.get("risk_bias") or "")
    if rb == "risk_off":
        return float(tc.get("risk_off", 0.45))
    if rb == "risk_on":
        return float(tc.get("risk_on", 0.80))
    return float(tc.get("default", 0.60))


def _nq_ceiling(nq: Optional[Dict], cfg: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """Map NQ driver daily read to an implied observation ceiling."""
    cfg = cfg or _DEFAULT_CFG
    nc = cfg.get("nq_ceilings") or {
        "missing": None,
        "veto": 0.20,
        "risk_off": 0.40,
        "mixed": 0.55,
        "risk_on": 0.75,
        "default": 0.60,
    }
    if not nq:
        return nc.get("missing", None)
    driver = nq.get("driver") or {}
    if driver.get("veto") or nq.get("pressure_override"):
        sid = driver.get("state_id")
        if sid in (6, 12) or driver.get("veto"):
            return float(nc.get("veto", 0.20))
        return float(nc.get("risk_off", 0.40))
    bias = driver.get("risk_bias") or nq.get("risk_bias") or ""
    if bias == "risk_off":
        return float(nc.get("risk_off", 0.40))
    if bias == "risk_on":
        return float(nc.get("risk_on", 0.75))
    if bias == "mixed":
        return float(nc.get("mixed", 0.55))
    return float(nc.get("default", 0.60))




def build_rates_stress_from_denom(
    denom: Optional[Dict],
    *,
    cfg: Optional[Dict[str, Any]] = None,
    warnings: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Rates snapshot. Default bind_mode=shadow (diagnostic only)."""
    cfg = cfg or _DEFAULT_CFG
    rs_cfg = dict(cfg.get("rates_stress") or {})
    mode = str(rs_cfg.get("bind_mode", "shadow")).lower()
    if rs_cfg.get("enabled", True) is False or mode == "off":
        return None
    warnings = warnings if warnings is not None else []
    try:
        from core.rates_stress import compute_rates_stress_series, params_from_mapping
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"rates_stress import failed: {exc}")
        return None

    params = params_from_mapping(rs_cfg)
    data_dir = REPO_ROOT / "data"
    try:
        import pandas as pd

        def _load(name: str, col: str):
            fp = data_dir / name
            if not fp.exists():
                return None
            df = pd.read_csv(fp)
            df["observation_date"] = pd.to_datetime(df["observation_date"])
            s = df.set_index("observation_date")[col]
            return pd.to_numeric(s, errors="coerce").sort_index()

        n30 = _load("_nom30y_daily.csv", "DGS30")
        tips = _load("_tips_daily.csv", "DFII10")
        if n30 is None:
            warnings.append("rates_stress: missing _nom30y_daily.csv; leg skipped")
            return None
        frame = pd.DataFrame({"nominal_30y": n30})
        if tips is not None:
            frame["tips_yield"] = tips.reindex(frame.index).ffill()
        frame = frame.dropna(subset=["nominal_30y"])
        series = compute_rates_stress_series(frame, params)
        last = series.iloc[-1]
        try:
            from core.denom_ceiling import classify_denom_tier

            block = cfg.get("denominator_ceilings") or {}
            policy = cfg.get("denom_policy") or {}
            st = (denom or {}).get("main_state") or (denom or {}).get("state")
            tier = classify_denom_tier(st, block, policy.get("state_tier_map"))
        except Exception:
            tier = "unknown"
        engaged = bool(last["engaged"])
        denom_tight = tier in ("crisis", "tight")
        b_zone = bool(engaged and not denom_tight)
        return {
            "rates_cap": float(last["rates_cap"]),
            "engaged": engaged,
            "reason": str(last["raw_reason"]),
            "nominal_30y_z5": None
            if pd.isna(last["nominal_30y_z5"])
            else round(float(last["nominal_30y_z5"]), 4),
            "nominal_30y_pct": None
            if pd.isna(last["nominal_30y_pct"])
            else round(float(last["nominal_30y_pct"]), 2),
            "as_of": str(series.index[-1].date())
            if hasattr(series.index[-1], "date")
            else str(series.index[-1]),
            "source": "fred_cache",
            "bind_mode": mode,
            "denom_tier": tier,
            "b_zone": b_zone,
            "diagnostic_only": mode == "shadow",
        }
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"rates_stress history failed: {exc}")
        z5 = (denom or {}).get("z5") or {}
        return {
            "rates_cap": 1.0,
            "engaged": False,
            "reason": "fallback_no_history",
            "nominal_30y_z5": z5.get("n30"),
            "nominal_30y_pct": None,
            "source": "denom_z5_fallback",
            "bind_mode": mode,
            "b_zone": False,
            "diagnostic_only": mode == "shadow",
        }


def _rates_should_bind(rates: Optional[Dict], cfg: Optional[Dict[str, Any]] = None) -> bool:
    cfg = cfg or _DEFAULT_CFG
    rs = cfg.get("rates_stress") or {}
    mode = str(rs.get("bind_mode", "shadow")).lower()
    if not rates or mode in ("shadow", "off", ""):
        return False
    if not rates.get("engaged"):
        return False
    if mode == "hard":
        return True
    if mode == "b_zone_only":
        return bool(rates.get("b_zone"))
    return False


def _rates_ceiling(
    rates: Optional[Dict],
    cfg: Optional[Dict[str, Any]] = None,
) -> Optional[float]:
    """Hard-bind cap only when bind_mode allows; shadow returns None."""
    cfg = cfg or _DEFAULT_CFG
    rs_cfg = cfg.get("rates_stress") or {}
    if rs_cfg.get("enabled", True) is False or not rates:
        return None
    if not _rates_should_bind(rates, cfg):
        return None
    mode = str(rs_cfg.get("bind_mode", "shadow")).lower()
    try:
        c = float(rates.get("rates_cap"))
    except (TypeError, ValueError):
        return None
    if c <= 0:
        return None
    if mode == "b_zone_only":
        bcap = float(rs_cfg.get("b_zone_cap", rs_cfg.get("cap_level_only", 0.65)))
        c = min(c, bcap)
    return min(1.0, c)



def _qqq_es_ret20(report_dir: Path, warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    """Best-effort 20d total return for QQQ/ES proxies from local caches."""
    warnings = warnings if warnings is not None else []
    out: Dict[str, Any] = {"qqq_ret_20": None, "es_ret_20": None, "qqq_closes": None}
    try:
        import pandas as pd
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"ppo equity import failed: {exc}")
        return out

    def _ret20(candidates: List[Path]) -> Optional[float]:
        for fp in candidates:
            if not fp.exists():
                continue
            try:
                df = pd.read_csv(fp)
            except Exception:
                continue
            cols = {c.lower(): c for c in df.columns}
            dcol = cols.get("observation_date") or cols.get("date") or df.columns[0]
            ccol = cols.get("close") or cols.get("c") or df.columns[1]
            s = df[[dcol, ccol]].copy()
            s[dcol] = pd.to_datetime(s[dcol], errors="coerce")
            s[ccol] = pd.to_numeric(s[ccol], errors="coerce")
            s = s.dropna().sort_values(dcol)
            if len(s) < 25:
                continue
            px = s[ccol].astype(float).values
            if px[-1] <= 0 or px[-21] <= 0:
                continue
            return float(px[-1] / px[-21] - 1.0)
        return None

    data = REPO_ROOT / "data"
    qqq_paths = [
        data / "_eq_qqq.csv",
        data / "_2022_eq_qqq.csv",
        report_dir / "_eq_qqq.csv",
    ]
    out["qqq_ret_20"] = _ret20(qqq_paths)
    out["es_ret_20"] = _ret20([
        data / "_eq_spx.csv",
        data / "_2022_eq_spy.csv",
        data / "_eq_spy.csv",
    ])
    try:
        import pandas as pd
        lookback = 80
        for fp in qqq_paths:
            if not fp.exists():
                continue
            df = pd.read_csv(fp)
            cols = {c.lower(): c for c in df.columns}
            dcol = cols.get("observation_date") or cols.get("date") or df.columns[0]
            ccol = cols.get("close") or cols.get("c") or df.columns[1]
            s = df[[dcol, ccol]].copy()
            s[dcol] = pd.to_datetime(s[dcol], errors="coerce")
            s[ccol] = pd.to_numeric(s[ccol], errors="coerce")
            s = s.dropna().sort_values(dcol)
            if len(s) >= 30:
                out["qqq_closes"] = [float(x) for x in s[ccol].astype(float).tolist()[-lookback:]]
                break
    except Exception:
        pass
    return out


def build_positioning_path(
    denom: Optional[Dict],
    rates: Optional[Dict],
    nq: Optional[Dict],
    *,
    cfg: Optional[Dict[str, Any]] = None,
    prev_combined: Optional[Dict[str, Any]] = None,
    report_dir: Optional[Path] = None,
    warnings: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Build PPO snapshot for consolidator."""
    cfg = cfg or _DEFAULT_CFG
    pp = dict(cfg.get("positioning_path") or {})
    if pp.get("enabled", True) is False:
        return None
    warnings = warnings if warnings is not None else []
    try:
        from core.positioning_path import (
            bind_path_day,
            classify_path,
            finalize_snapshot,
            params_from_mapping,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"positioning_path import failed: {exc}")
        return None

    params = params_from_mapping(pp)
    eq = _qqq_es_ret20(report_dir or DEFAULT_REPORT_DIR, warnings)
    z5 = (denom or {}).get("z5") or {}
    tips_z = z5.get("tips") if isinstance(z5, dict) else None
    n30_z = z5.get("n30") if isinstance(z5, dict) else None
    if rates and rates.get("nominal_30y_z5") is not None:
        n30_z = rates.get("nominal_30y_z5")
    features = {
        "tips_z5": tips_z,
        "n30_z5": n30_z,
        "hy_z5": z5.get("cs") if isinstance(z5, dict) else None,
        "qqq_ret_20": eq.get("qqq_ret_20"),
        "es_ret_20": eq.get("es_ret_20"),
        "qqq_closes": eq.get("qqq_closes"),
        "gold_z5": z5.get("gold") if isinstance(z5, dict) else None,
        "bei_d5": None,
        "rates_engaged": bool((rates or {}).get("engaged")),
        "denom_tier": (prev_combined or {}).get("denom_tier") if prev_combined else None,
        "denom_state": (denom or {}).get("main_state") or (denom or {}).get("state"),
        "main_state": (denom or {}).get("main_state") or (denom or {}).get("state"),
        "settle_ok": True,
    }
    try:
        from core.denom_ceiling import classify_denom_tier

        block = cfg.get("denominator_ceilings") or {}
        policy = cfg.get("denom_policy") or {}
        features["denom_tier"] = classify_denom_tier(
            features.get("main_state"), block, policy.get("state_tier_map")
        )
    except Exception:
        pass

    raw = classify_path(features, params)
    prev_ppo = None
    if isinstance(prev_combined, dict):
        prev_ppo = prev_combined.get("positioning_path_state")
    hyst = bind_path_day(
        str(raw.get("path") or "MIXED"),
        prev_held=(prev_ppo or {}).get("held_path") if isinstance(prev_ppo, dict) else None,
        up_streak=int((prev_ppo or {}).get("up_streak") or 0) if isinstance(prev_ppo, dict) else 0,
        dn_streak=int((prev_ppo or {}).get("dn_streak") or 0) if isinstance(prev_ppo, dict) else 0,
        confirm_days=params.confirm_days,
        exit_days=params.exit_days,
        deteriorate_confirm_days=getattr(params, "deteriorate_confirm_days", 3),
    )
    snap = finalize_snapshot(raw, held_path=str(hyst["held_path"]), hyst=hyst, params=params)
    snap["positioning_path_state"] = {
        "held_path": hyst["held_path"],
        "raw_path": hyst["raw_path"],
        "up_streak": hyst["up_streak"],
        "dn_streak": hyst["dn_streak"],
        "hyst": hyst["hyst"],
    }
    if nq:
        drv = nq.get("driver") or nq
        snap["nq_state"] = drv.get("state_name") or nq.get("state_name")
        snap["nq_bias"] = drv.get("risk_bias") or nq.get("risk_bias")
    return snap


def compute_combined_budget(
    denom: Optional[Dict],
    tech: Optional[Dict],
    theme: Optional[Dict],
    nq: Optional[Dict] = None,
    rates: Optional[Dict] = None,
    ppo: Optional[Dict] = None,
    *,
    data_quality: str = "ok",
    nq_quality: str = "ok",
    cfg: Optional[Dict[str, Any]] = None,
    prev_combined: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """D + frozen C + rates soft/diagnostic combined ceiling."""
    cfg = cfg or _DEFAULT_CFG
    tech_default = float(cfg.get("tech_default_ceiling", 0.80))
    denom_present = denom is not None
    tech_present = tech is not None and ((tech.get("decision") or {}).get("risk_budget") is not None)
    theme_present = theme is not None
    nq_present = nq is not None
    rates_present = rates is not None
    theme_usable = theme_present and data_quality not in (
        "stale", "degraded", "stale_degraded", "missing",
    )
    nq_usable = nq_present and nq_quality not in (
        "stale", "degraded", "stale_degraded", "missing", "bad",
    )

    denom_bind = (
        bind_denom_ceiling_day(denom, cfg=cfg, prev_combined=prev_combined)
        if denom_present
        else None
    )
    policy = cfg.get("denom_policy") or {}
    denom_c = float(denom_bind["ceiling"]) if denom_bind and policy.get("bind", True) else None
    tech_c = float((tech.get("decision") or {}).get("risk_budget")) if tech_present else None
    theme_c = _theme_ceiling(theme, cfg) if theme_usable else None
    nq_c = _nq_ceiling(nq, cfg) if nq_usable else None
    rates_c = _rates_ceiling(rates, cfg) if rates_present else None
    ppo_c = None
    if ppo and str((cfg.get("positioning_path") or {}).get("mode", "flag_only")).lower() == "soft_cap":
        sc = ppo.get("soft_cap")
        if sc is None and ppo.get("path") in ("DETERIOR", "BAD_EASE"):
            try:
                from core.positioning_path import path_soft_cap, params_from_mapping

                ppo_c = path_soft_cap(
                    str(ppo.get("path")),
                    params_from_mapping(cfg.get("positioning_path") or {}),
                )
            except Exception:
                ppo_c = None
        else:
            try:
                ppo_c = float(sc) if sc is not None else None
            except (TypeError, ValueError):
                ppo_c = None

    theme_status = (
        "excluded_quality" if theme_present and not theme_usable
        else ("missing" if not theme_present else "ok")
    )
    nq_status = (
        "excluded_quality" if nq_present and not nq_usable
        else ("missing" if not nq_present else "ok")
    )
    rs_mode = str((cfg.get("rates_stress") or {}).get("bind_mode", "shadow")).lower()
    rates_status = (
        "missing" if not rates_present
        else ("shadow" if rs_mode == "shadow" else ("bound" if rates_c is not None else "not_binding"))
    )

    labels: List[Tuple[str, float]] = []
    if denom_c is not None:
        labels.append(("分母", denom_c))
    if tech_c is not None:
        labels.append(("减震器", tech_c))
    if theme_c is not None:
        labels.append(("主题", theme_c))
    if nq_c is not None:
        labels.append(("NQ动因", nq_c))
    if rates_c is not None and rates_c < 0.999:
        labels.append(("利率", rates_c))
    if ppo_c is not None and ppo_c < 0.999:
        labels.append(("定位", float(ppo_c)))

    complete = denom_present and tech_present and theme_usable
    base_meta = {
        "denominator_ceiling_raw": None if not denom_bind else round(float(denom_bind["raw_ceiling"]), 2),
        "denominator_ceiling_held": None if not denom_bind else round(float(denom_bind["ceiling"]), 2),
        "denom_tier": None if not denom_bind else denom_bind.get("tier"),
        "denom_hysteresis": None if not denom_bind else denom_bind.get("hysteresis"),
        "denom_tight_streak": None if not denom_bind else denom_bind.get("tight_streak"),
        "denom_loose_streak": None if not denom_bind else denom_bind.get("loose_streak"),
        "rates_bind_mode": rs_mode,
        "rates_shadow": None if not rates else {
            "engaged": rates.get("engaged"),
            "rates_cap": rates.get("rates_cap"),
            "reason": rates.get("reason"),
            "b_zone": rates.get("b_zone"),
            "denom_tier": rates.get("denom_tier"),
            "as_of": rates.get("as_of"),
        },
        "tech_policy": (cfg.get("tech_policy") or {}).get("mode", "frozen_c_tier"),
        "positioning_path": None
        if not ppo
        else {
            "path": ppo.get("path"),
            "path_zh": ppo.get("path_zh"),
            "gate": ppo.get("gate"),
            "skew": ppo.get("skew"),
            "mode": ppo.get("mode"),
            "soft_cap": ppo_c if ppo_c is not None else ppo.get("soft_cap"),
        },
        "positioning_path_state": (ppo or {}).get("positioning_path_state"),
        "ppo_ceiling": None if ppo_c is None else round(float(ppo_c), 2),
    }

    if not labels:
        return {
            "denominator_ceiling": None if not denom_bind else round(float(denom_bind["ceiling"]), 2),
            "tech_ceiling": tech_c if tech_c is not None else tech_default,
            "theme_ceiling": theme_c,
            "nq_ceiling": nq_c,
            "rates_ceiling": rates_c,
            "combined_budget": None,
            "binding": [],
            "complete": False,
            "degraded": True,
            "theme_status": theme_status,
            "nq_status": nq_status,
            "rates_status": rates_status,
            "reason": "no_ceilings_available",
            **base_meta,
        }

    combined = min(v for _, v in labels)
    binders = [lab for lab, val in labels if abs(val - combined) < 1e-9]
    degraded = (not complete) or data_quality in (
        "stale", "degraded", "stale_degraded", "missing",
    ) or (nq_present and not nq_usable)
    return {
        "denominator_ceiling": None if denom_c is None else round(denom_c, 2),
        "tech_ceiling": round(tech_c if tech_c is not None else tech_default, 2),
        "theme_ceiling": None if theme_c is None else round(theme_c, 2),
        "nq_ceiling": None if nq_c is None else round(nq_c, 2),
        "rates_ceiling": None if rates_c is None else round(rates_c, 2),
        "combined_budget": round(combined, 2),
        "binding": binders,
        "complete": complete,
        "degraded": degraded,
        "theme_status": theme_status,
        "nq_status": nq_status,
        "rates_status": rates_status,
        "reason": "ok" if complete and not degraded else "partial_or_degraded",
        **base_meta,
    }


def try_load_sentiment_shadow(
    report_dir: Path, date_str: str, warnings: List[str]
) -> Optional[Dict[str, Any]]:
    candidates = [
        report_dir / f"sentiment_shadow_{date_str}.json",
        REPO_ROOT / "vault" / "shadow" / f"sentiment_{date_str}.json",
    ]
    for p in candidates:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"sentiment shadow parse failed ({p.name}): {exc}")
    jsonl = REPO_ROOT / "vault" / "shadow" / "sentiment_shadow.jsonl"
    if jsonl.exists():
        try:
            for line in reversed(jsonl.read_text(encoding="utf-8").strip().splitlines()):
                if not line.strip():
                    continue
                obj = json.loads(line)
                if str(obj.get("as_of") or "")[:10] == date_str:
                    return obj
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"sentiment jsonl read failed: {exc}")
    return None



def build_re_risk_snapshot(
    combined: Optional[Dict[str, Any]],
    ppo: Optional[Dict[str, Any]],
    *,
    cfg: Optional[Dict[str, Any]] = None,
    prev_combined: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Scheme A dual budget on top of defense combined ceiling."""
    cfg = cfg or _DEFAULT_CFG
    rr_cfg = dict(cfg.get("re_risk") or {})
    if rr_cfg.get("enabled", True) is False:
        return None
    if not combined or combined.get("combined_budget") is None:
        return None
    try:
        from core.re_risk import compute_re_risk, params_from_mapping
    except Exception:
        return None

    params = params_from_mapping(rr_cfg)
    prev_rr = None
    if isinstance(prev_combined, dict):
        prev_rr = prev_combined.get("re_risk")
    rates_shadow = combined.get("rates_shadow") if isinstance(combined, dict) else None
    qqq_r20 = None
    if ppo and isinstance(ppo.get("metrics"), dict):
        qqq_r20 = ppo["metrics"].get("qqq_ret_20")
    snap = compute_re_risk(
        defense_ceiling=float(combined["combined_budget"]),
        prev_state=prev_rr if isinstance(prev_rr, dict) else None,
        denom_tier=combined.get("denom_tier"),
        denom_hyst=combined.get("denom_hysteresis"),
        tech_ceiling=combined.get("tech_ceiling"),
        ppo=ppo,
        rates_shadow=rates_shadow,
        qqq_ret_20=qqq_r20,
        curve_skew=(ppo or {}).get("skew") if ppo else None,
        params=params,
    )
    return snap



def build_plain_advice(
    theme: Optional[Dict],
    tech: Optional[Dict],
    denom: Optional[Dict],
    nq: Optional[Dict],
    synth: Dict[str, Any],
    combined: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Pine V3 读法 -> 白话交易建议（观察纪律，非下单信号）。"""
    drv = (nq or {}).get("driver") or {}
    hard_veto = bool(drv.get("veto") or synth.get("nq_veto"))
    pressure = bool((nq or {}).get("pressure_override") or synth.get("nq_pressure_override"))
    nq_state = drv.get("state_name") or synth.get("nq_state") or "—"
    nq_bias = drv.get("risk_bias") or synth.get("nq_bias") or "none"
    driver_mod = drv.get("driver_mod") or synth.get("nq_driver_mod") or "—"
    dont_do = drv.get("dont_do") or synth.get("nq_dont_do") or ""
    invalid = drv.get("invalid_if") or ""
    playbook = drv.get("playbook") or ""
    attitude = drv.get("attitude") or "—"
    participation = drv.get("participation") or "—"
    opp_over = bool(drv.get("opposing_over_dominant"))
    sid = drv.get("state_id")

    dec = (tech or {}).get("decision") or {}
    tech_budget = dec.get("risk_budget")
    damp = bool(dec.get("dampener_active"))
    dom = (theme or {}).get("dominant_theme") or {}
    theme_name = dom.get("name") or "无主导"
    theme_bias = dom.get("risk_bias") or (theme or {}).get("risk_bias") or "none"
    denom_state = ""
    if denom:
        denom_state = str(denom.get("state") or denom.get("main_state") or "")
    denom_dont = (denom or {}).get("dont_do") or ""

    if hard_veto:
        stance = f"防守优先：NQ 硬否决（{nq_state}），多头打法暂停"
        posture = "defend"
    elif pressure or nq_bias == "risk_off" or (
        tech_budget is not None and float(tech_budget) <= 0.35
    ):
        stance = f"防守日：{nq_state} · 降杠杆观望，不接飞刀"
        posture = "defend"
    elif synth.get("bias") == "risk_on" and synth.get("alignment") == "共振":
        stance = f"顺风观察：{nq_state} · 仅在失效未触发时持有，禁做优先"
        posture = "risk_on_observe"
    elif synth.get("bias") == "mixed" or nq_bias == "mixed":
        stance = f"混合日：{nq_state} · 控制仓位，先看禁做再看打法"
        posture = "mixed"
    else:
        stance = f"等待确认：{nq_state} · 轻仓或观望"
        posture = "wait"

    mod = str(driver_mod)
    if "否决" in mod:
        mod_plain = "动因层否决级：对应方向的进攻打法暂停，不是普通减半"
    elif "逆风减半" in mod:
        mod_plain = "逆风减半：若仍想参与，仓位/进攻性先砍半，不是开多许可证"
    elif "对抗" in mod or opp_over:
        mod_plain = "对抗力已压过主导：状态可能靠切换惯性撑着——减仓或收紧止损的正式理由"
    elif "顺风足额" in mod:
        mod_plain = "顺风足额（观察）：框架偏多，但仍以「今天不做」和失效条件为先"
    elif "顶压" in mod or "空头暂停" in mod:
        mod_plain = "顶压上涨：空头打法暂停，等投降信号，不抢空"
    elif "过渡" in str(playbook) or sid in (13, 14, 15):
        mod_plain = "过渡态：理由与价格未对齐，禁止提前重仓押方向"
    else:
        mod_plain = f"动因修正：{mod}"

    do_list: List[str] = []
    dont_list: List[str] = []
    if dont_do:
        for part in re.split(r"[；;]", str(dont_do)):
            part = part.strip()
            if part:
                dont_list.append(part)
    if denom_dont:
        for part in re.split(r"[；;，,]", str(denom_dont)):
            part = part.strip()
            if part and part not in dont_list:
                dont_list.append(f"分母纪律：{part}")

    if posture == "defend":
        do_list.extend(
            [
                "降杠杆、降久期，优先减 NQ/SOXX 贝塔暴露",
                "以观望/防守为主；若交易仅小仓短持并设硬止损",
                f"把失效条件当重新评估开关：{invalid or '见 NQ 面板失效行'}",
            ]
        )
        if damp or (tech_budget is not None and float(tech_budget) <= 0.50):
            do_list.append(
                f"科技减震器激活（budget={tech_budget}）：科技多头敞口按更严上限管理"
            )
    elif posture == "risk_on_observe":
        do_list.extend(
            [
                "仅在失效条件未触发时考虑持有或回撤轻仓，而非追高满仓",
                "用参与度验成色：全员参与优于 NQ 独行",
                f"打法框架参考：{playbook or '顺风框架'}",
            ]
        )
    elif posture == "mixed":
        do_list.extend(
            [
                "控制总仓位，先执行禁做清单再考虑方向",
                f"态度={attitude} · 参与度={participation}：不对齐时减少交易频率",
            ]
        )
    else:
        do_list.extend(
            [
                "轻仓或空仓等待主题/价格对齐",
                "避免在无主题或力量酝酿窗口硬编故事重仓",
            ]
        )

    conflicts: List[str] = []
    if theme_bias == "risk_on" and (nq_bias == "risk_off" or hard_veto or pressure):
        conflicts.append(
            f"主题「{theme_name}」偏多，但 NQ 为 {nq_state}（{nq_bias}）"
        )
        arb = "跨腿冲突时以更严一侧为准：跟 NQ 动因/减震器纪律，不把主题 risk_on 当满仓许可证"
    elif denom_state and ("分裂" in denom_state or "未确认" in denom_state) and theme_bias == "risk_on":
        conflicts.append(f"分母 {denom_state} vs 主题偏多")
        arb = "分母未确认时，主题叙事可看、仓位仍收敛"
    elif synth.get("divergences"):
        arb = "存在背离警示：先消化背离，再谈进攻"
    else:
        arb = "四腿无尖锐冲突时，仍以 NQ「今天不做」> 打法框架 > 叙事"

    cb = (combined or {}).get("combined_budget")
    binding = ", ".join((combined or {}).get("binding") or []) or "—"
    if cb is None:
        budget_line = "合成上限不可用（数据不足/降级）——只陈述纪律，不给完整进攻预算"
    else:
        budget_line = f"观察层合成风险上限约 {cb}（约束：{binding}）；实盘仍以 kernel decide() 为准"

    pine_gap = [
        "日频代理不含 15m/1h/4h 三周期打法行与事件静默窗",
        "不替代 TradingView 上 NQ 动因 V3 盘中面板",
        "「今天不做」优先级高于打法；本栏是战术纪律翻译，不是买卖信号",
    ]

    return {
        "headline": stance,
        "posture": posture,
        "stance": stance,
        "driver_mod_plain": mod_plain,
        "playbook": playbook or "—",
        "attitude": attitude,
        "participation": participation,
        "do": do_list,
        "dont": dont_list or ["（NQ 未给出 dont_do）"],
        "invalid_if": invalid or "—",
        "conflicts": conflicts,
        "arbitration": arb,
        "budget_line": budget_line,
        "pine_gap": pine_gap,
        "nq_state": nq_state,
        "hard_veto": hard_veto,
        "pressure_override": pressure,
    }


def format_plain_advice_md(advice: Dict[str, Any]) -> List[str]:
    """Markdown lines for section ⑥."""
    L: List[str] = []
    L.append("## ⑥ 白话交易建议（Pine V3 读法 · 非信号）")
    L.append("")
    L.append(f"> **一句话**：{advice.get('headline') or '—'}")
    L.append("")
    L.append(f"- **NQ 状态**：{advice.get('nq_state') or '—'} ｜ posture=`{advice.get('posture')}`")
    flag = (
        "硬否决"
        if advice.get("hard_veto")
        else ("压力覆盖" if advice.get("pressure_override") else "否")
    )
    L.append(f"- **硬否决/压力**：{flag}")
    L.append(f"- **动因修正（白话）**：{advice.get('driver_mod_plain') or '—'}")
    L.append(f"- **打法框架**：{advice.get('playbook') or '—'}")
    L.append(
        f"- **态度 / 参与度**：{advice.get('attitude') or '—'} / {advice.get('participation') or '—'}"
    )
    L.append("")
    L.append("### 今天不做（最高优先）")
    L.append("")
    for x in advice.get("dont") or []:
        L.append(f"- ❌ {x}")
    L.append("")
    L.append("### 可以考虑")
    L.append("")
    for x in advice.get("do") or []:
        L.append(f"- ✅ {x}")
    L.append("")
    L.append(f"- **失效再评估**：{advice.get('invalid_if') or '—'}")
    L.append(f"- **预算锚**：{advice.get('budget_line') or '—'}")
    if advice.get("conflicts"):
        L.append("")
        L.append("### 跨腿冲突")
        L.append("")
        for c in advice["conflicts"]:
            L.append(f"- ⚠ {c}")
    L.append(f"- **仲裁**：{advice.get('arbitration') or '—'}")
    L.append("")
    L.append("### 与真·Pine 盘中面板的差距")
    L.append("")
    for g in advice.get("pine_gap") or []:
        L.append(f"- {g}")
    L.append("")
    return L



def build_report(
    date_str: str,
    denom: Optional[Dict],
    tech: Optional[Dict],
    theme: Optional[Dict],
    synth: Dict[str, Any],
    combined: Optional[Dict[str, Any]] = None,
    *,
    warnings: Optional[List[str]] = None,
    sentiment: Optional[Dict[str, Any]] = None,
    nq: Optional[Dict[str, Any]] = None,
    sector: Optional[Dict[str, Any]] = None,
    shock: Optional[Dict[str, Any]] = None,
    advice: Optional[Dict[str, Any]] = None,
) -> str:
    L: List[str] = []
    L.append(f"# Daily Macro 综合观察 · {date_str}")
    L.append("")
    L.append("> **观察合成 · 非交易指令**。实盘预算以 kernel `decide()` 为准。")
    L.append("")
    sector_date = (sector or {}).get("as_of") if sector else None
    L.append(
        f"- **主题 as_of**: {synth.get('theme_date') or '—'} ｜ "
        f"**减震器 as_of**: {synth.get('tech_date') or '—'} ｜ "
        f"**分母 as_of**: {synth.get('denom_date') or '—'} ｜ "
        f"**NQ as_of**: {synth.get('nq_date') or '—'}"
        + (f" ｜ **板块轮动 as_of**: {sector_date}" if sector_date else "")
    )
    L.append(
        f"- **alignment / bias / 数据质量**: "
        f"{synth.get('alignment', '—')} / {synth.get('bias', '—')} / {synth.get('data_quality', '—')}"
    )
    if warnings:
        L.append("")
        L.append("## ⚠ 运行警告")
        L.append("")
        for w in warnings:
            L.append(f"- {w}")
        L.append("")

    L.append("## ① 分母状态")
    L.append("")
    if denom:
        L.append(f"- **主状态**: {denom.get('main_state') or '—'}")
        L.append(f"- **标签**: {synth.get('denom_tag', '—')}")
        if denom.get("four_quad"):
            L.append(f"- **四象限**: {denom.get('four_quad')}")
        if denom.get("dont_do"):
            L.append(f"- **今天不做什么**: {denom.get('dont_do')}")
        if denom.get("trigger_hint"):
            L.append(f"- **触发提示**: {denom.get('trigger_hint')}")
        L.append(f"- **来源**: {denom.get('source', '—')}")
    else:
        L.append("> 分母状态未读取。")
    L.append("")

    L.append("## ② 科技减震器")
    L.append("")
    if tech:
        dec = tech.get("decision") or {}
        L.append(
            f"- **risk_budget**: **{synth.get('risk_budget')}** （{synth.get('equity_stress')}）"
        )
        L.append(f"- **减震器状态**: {'激活' if synth.get('dampener_active') else '未触发'}")
        L.append(
            f"- **权限 / 原因**: `{dec.get('authority', '—')}` / `{dec.get('reason_code', '—')}`"
        )
        traj = tech.get("trajectory")
        if traj:
            _lab_cn = {"rebuilding": "反弹恢复中", "bottoming": "摸底横盘", "deepening": "探底加深"}
            _lab = _lab_cn.get(traj.get("label"), traj.get("label"))
            L.append(
                f"- **回撤轨迹（斜率/方向）**: **{_lab}** ｜ 谷位 {traj['trough_date']}="
                f"{traj['trough_close']} ｜ 自谷反弹 +{traj['recovery_from_trough_pct']}% ｜ "
                f"近5日回撤斜率 +{traj['drawdown_slope_5d_pp']}pp/日"
            )
            L.append(
                f"- **解读**: 喂内核的 tech_drawdown={tech.get('tech_drawdown')} 为**迟滞平滑值**，"
                f"滞后于价格反弹；原始(非迟滞)20日峰值回撤仅 {traj['naive_20d_peak_dd_pct']}%。"
                f"方向以轨迹为准，勿因单一回撤数字误判为仍在下跌。"
            )
        L.append("")
        L.append(
            f"> 完整读数见 `tech_dampener_decision_{date_str}.md` + `tech_drawdown_{date_str}.json`"
        )
    else:
        L.append("> 科技减震器未运行。")
    L.append("")

    # ②-b 冲击吸收旁证（分母压力交叉校验，观察+报警，不进主状态机，不动合成预算）
    L.append("## ②-b 冲击吸收旁证（分母交叉校验 · 观察+报警）")
    L.append("")
    if shock:
        legs = shock.get("legs", {})
        r = shock.get("record_20d", {})
        fp = shock.get("fingerprints", {})
        cross = (synth or {}).get("shock_absorption") or {}
        L.append(
            f"- **大状态**: {shock.get('fragility_state')} (level={shock.get('fragility_level')}) ｜ "
            f"主软肋: {shock.get('weak_link')} ｜ 今日: {shock.get('day_text')}"
        )
        L.append(
            f"- **20日记录**: 冲击 {r.get('hit')} · 加剧 {r.get('strain')} · 破裂 {r.get('fail')}"
            f"（利率 {r.get('rate_fail')} / 信用 {r.get('cred_fail')} / 美元 {r.get('dxy_fail')} / 波动 {r.get('vol_fail')}）"
        )

        def _leg_line(name: str, leg: Dict[str, Any]) -> str:
            if leg.get("abstain"):
                return f"  - {name}: 缺数弃权"
            if not leg.get("hit"):
                return f"  - {name}: 无冲击 (z {leg.get('z')})"
            extra = ""
            if name == "利率" and leg.get("bp") is not None:
                extra = f" ({leg['bp']}bp {'长端' if leg.get('long_end') else '腹部'})"
            elif name == "美元" and leg.get("ret_pct") is not None:
                extra = f" ({leg['ret_pct']}%)"
            elif name == "信用" and leg.get("qual_z") is not None:
                extra = f" · 质量差 z {leg['qual_z']}"
            elif name == "波动" and leg.get("pct") is not None:
                extra = f" · 水位 {leg['pct']}分位"
            return (f"  - {name}: {leg.get('dir', '')} · {leg.get('code_text')} · "
                    f"z {leg.get('z')} · 损伤比 q {leg.get('q')}{extra}")

        L.append("- **四条冲击腿**:")
        L.append(_leg_line("利率", legs.get("rate", {})))
        L.append(_leg_line("美元", legs.get("dxy", {})))
        L.append(_leg_line("信用", legs.get("credit", {})))
        L.append(_leg_line("波动", legs.get("vol", {})))
        L.append(
            f"- **指纹**: 保证金抛售={'出现' if fp.get('margin_call') else '未出现'} ｜ "
            f"股债汇三杀={'出现' if fp.get('sell_america') else '未出现'} ｜ "
            f"BTC金丝雀={'报警' if fp.get('btc_canary') else '平静'}"
        )
        agree = cross.get("agree")
        if agree is False:
            L.append("- **与分母交叉**: ⚠ 冲突（见 ④ 背离警示）")
        elif agree is True:
            L.append("- **与分母交叉**: 同向，无硬冲突")
        L.append("")
        L.append(
            f"> 冲击吸收判读见 `shock_absorption_{shock.get('date')}.md` + `.json`。"
            "本段仅为分母压力旁证，不进主状态机，不动合成预算 min。门槛初值未标定(SA-1)。"
        )
    else:
        L.append("> 冲击吸收旁证未运行/不可用。")
    L.append("")

    L.append("## ③ 主题状态机")
    L.append("")
    if theme:
        dom = theme.get("dominant_theme")
        if dom:
            conf = dom.get("confidence") or {}
            L.append(f"- **市场主题**: {dom.get('name')}")
            L.append(
                f"- **家族**: {dom.get('family')} ｜ **置信**: "
                f"n3={conf.get('n3')}, 持续 {conf.get('persist_days')} 日"
            )
            L.append(
                f"- **risk_bias**: {dom.get('risk_bias') or theme.get('risk_bias')} ｜ "
                f"pressure_override="
                f"{'是' if (dom.get('pressure_override') or theme.get('pressure_override')) else '否'}"
            )
        else:
            L.append("- **市场主题**: 无主导主题 · 未形成共振")
            L.append("- **白话解读**: 各品种没有形成一致方向 —— 等待共振，别硬编故事")
        tq = synth.get("theme_quality") or theme.get("quality") or {}
        if tq:
            L.append(
                f"- **主题数据质量**: `{tq.get('data_quality', '—')}`"
                f" ｜ as_of={tq.get('as_of', theme.get('date'))}"
                f" ｜ lag={tq.get('lag_days', '—')}"
            )
        leaders = theme.get("today_leaders") or []
        if leaders:
            # today_leaders 的 z 是「5日动量异常度」，箭头由 z 符号决定，**不是当日涨跌**。
            # 旧版 json 无 ret1d_pct → 回退到 strength_table 按 label 关联补齐。
            ret_by_label = {}
            for row in (theme.get("strength_table") or []):
                if isinstance(row, dict) and row.get("label") is not None:
                    ret_by_label[str(row["label"])] = row.get("ret1d_pct")
            parts = []
            for item in leaders:
                lab = item.get("label", "?")
                direction = item.get("dir", "")
                try:
                    z_s = f"{float(item.get('z')):+.2f}"
                except (TypeError, ValueError):
                    z_s = str(item.get("z", ""))
                r1p = item.get("ret1d_pct")
                if r1p is None:
                    r1p = next(
                        (v for k, v in ret_by_label.items() if k.startswith(str(lab))),
                        None,
                    )
                try:
                    r1_s = "—" if r1p is None else f"{float(r1p):+.2f}%"
                except (TypeError, ValueError):
                    r1_s = "—"
                parts.append(f"{lab} z{z_s}{direction}（当日{r1_s}）")
            L.append(
                f"- **动量主角（5日动量z｜非当日涨跌）**: {'  '.join(parts)}"
            )
        fam = theme.get("families") or {}
        if fam:
            fam_items = [
                f"{k}: {('无' if not v else (v.get('theme') if isinstance(v, dict) else v))}"
                for k, v in fam.items()
            ]
            L.append(f"- **五题材分区**: {' ｜ '.join(fam_items)}")
        L.append("")
        L.append(f"> 完整读数见 `theme_state_machine_{synth.get('theme_date')}.md`")
    else:
        L.append("> 主题状态机未运行。")
    L.append("")

    if sentiment:
        L.append("## ③-b A股情绪影子（旁路，可选）")
        L.append("")
        cycle = sentiment.get("cycle") or {}
        L.append(
            f"- **stage**: {cycle.get('stage', sentiment.get('stage', '—'))} ｜ "
            f"**rebound**: {cycle.get('rebound_type', '—')}"
        )
        flags = sentiment.get("corroboration_flags") or sentiment.get("flags") or []
        if flags:
            L.append(f"- **flags**: {' '.join(f'`{f}`' for f in flags[:12])}")
        L.append("> Shadow only · 不修改 Kernel")
        L.append("")

    L.append("## ③-c NQ 动因（日频代理）")
    L.append("")
    if nq:
        drv = nq.get("driver") or {}
        L.append(
            f"- **状态**: {drv.get('state_name') or synth.get('nq_state') or '—'} "
            f"(id={drv.get('state_id', '—')}, {drv.get('side', '—')})"
        )
        hard_veto = bool(drv.get("veto"))
        pressure = bool(nq.get("pressure_override")) and not hard_veto
        flag = "硬否决" if hard_veto else ("压力覆盖" if pressure else "否")
        L.append(
            f"- **risk_bias**: {synth.get('nq_bias', drv.get('risk_bias'))} ｜ "
            f"veto/压力={flag} ｜ "
            f"mod={drv.get('driver_mod') or synth.get('nq_driver_mod') or '—'}"
        )
        L.append(
            f"- **净力量/态度/参与度**: {drv.get('net_force')} / {drv.get('attitude')} / {drv.get('participation')}"
        )
        L.append(
            f"- **主导 vs 对抗**: {drv.get('dominant_theme')} ({drv.get('dominant_score')}) vs "
            f"{drv.get('opposing_theme')} ({drv.get('opposing_score')})"
            + (" ⚠对抗已压过主导" if drv.get("opposing_over_dominant") else "")
        )
        L.append(f"- **股债体制**: {drv.get('bond_regime')} (corr={drv.get('bond_corr_nq_tlt_60d')})")
        L.append(f"- **打法框架**: {drv.get('playbook')}")
        L.append(f"- **今天不做**: {drv.get('dont_do') or synth.get('nq_dont_do') or '—'}")
        L.append(f"- **失效**: {drv.get('invalid_if') or '—'}")
        nqq = (nq.get("quality") or {})
        L.append(
            f"- **数据质量**: `{nqq.get('data_quality', synth.get('nq_quality', '—'))}`"
            f" ｜ missing={nqq.get('missing_legs', [])}"
        )
        L.append("")
        L.append(f"> 完整读数见 `nq_driver_{synth.get('nq_date') or date_str}.md`")
    else:
        L.append("> NQ 动因未运行。")
    L.append("")

    # ③-d 标普板块资金轮动（Pine v1.3m 复刻）— 互补美股比价视角，不入合成预算 min
    L.append("## ③-d 标普板块资金轮动（Pine v1.3m 复刻 · 美股视角）")
    L.append("")
    if sector:
        b = sector.get("bench") or {}
        L.append(
            f"- **基准(SPY) 分数/状态**: {b.get('score')} / {b.get('state')}"
            f" ｜ 绝对5D {b.get('abs5_pct')} ｜ 真广度 {b.get('breadth_pct')}% ｜ 原因: {b.get('reason')}"
        )
        themes = sorted(
            [t for t in (sector.get("themes") or []) if isinstance(t, dict)],
            key=lambda t: (t.get("score") if t.get("score") is not None else -1),
            reverse=True,
        )
        if themes:
            top = themes[:3]
            bottom = [t for t in themes if (t.get("score") or 0) < 45][-2:]
            if not bottom:
                bottom = themes[-2:]
            L.append(
                "- **最强板块(分数/状态/原因)**: "
                + "  ".join(
                    f"{t.get('name')} {t.get('score')}/{t.get('state')}/{t.get('reason')}"
                    for t in top
                )
            )
            L.append(
                "- **最弱板块(分数/状态/原因)**: "
                + "  ".join(
                    f"{t.get('name')} {t.get('score')}/{t.get('state')}/{t.get('reason')}"
                    for t in bottom
                )
            )
        attr = sorted(
            [a for a in (sector.get("attribution") or []) if isinstance(a, dict)],
            key=lambda a: (a.get("score") if a.get("score") is not None else -1),
            reverse=True,
        )
        if attr:
            a0 = attr[0]
            L.append(f"- **最强归因剧本**: {a0.get('name')} 分 {a0.get('score')} ｜ 核心信号: {a0.get('hint')}")
        cnt = sector.get("counts") or {}
        if cnt:
            L.append(
                f"- **板块计数**: 转正(5D相对>0) {cnt.get('pos_rel')}/11 ｜ "
                f"转弱(5D相对<0) {cnt.get('weak')}/11 ｜ 绝对下跌 {cnt.get('abs_down')}/11"
            )
        if sector.get("expired"):
            L.append(
                f"- ⚠ **引擎已过期**（有效期至 {sector.get('version')} 标注的到期日）；"
                "该板块轮动读数为过期引擎产出，仅供参考。"
            )
        L.append(
            "> 美股板块相对 SPY 比价的独立观测，与分母/减震器/主题/NQ 平行互补，**不参与合成预算 min**。"
            "基准走弱时主题高分只说明跌得少；基准「龙头抬轿」= 指数涨但过半板块没跟上，提防补跌。"
            "分数 75/60/45 为经验线，非下注指令。"
        )
        L.append("")
        L.append(f"> 完整读数见 `sector_rotation_{sector_date}.md` + `sector_rotation_{sector_date}.json`")
    else:
        L.append("> 标普板块轮动未运行/不可用。")
    L.append("")

    L.append("## ④ 综合研判（跨工具交叉验证）")
    L.append("")
    L.append(f"- **一致性**: {synth.get('consistency')}")
    L.append(
        f"- **alignment / bias**: {synth.get('alignment', '—')} / {synth.get('bias', '—')}"
    )
    L.append(
        f"- **数据质量**: {synth.get('data_quality', '—')}"
        f"{' ｜ 压力置顶' if synth.get('pressure_override') else ''}"
    )
    L.append(f"- **股市承压信号**: {'是' if synth.get('equity_down') else '否'}")
    if synth.get("divergences"):
        L.append("- **背离警示**:")
        for d in synth["divergences"]:
            L.append(f"  - ⚠ {d}")
    else:
        L.append("- **背离警示**: 无")
    L.append("")

    if combined:
        L.append("## ⑤ 合成风险预算上限（行动参考 · 非下单）")
        L.append("")
        L.append(
            f"- **分母上限**: {combined.get('denominator_ceiling')} ｜ "
            f"**减震器上限**: {combined.get('tech_ceiling')} ｜ "
            f"**主题上限**: {combined.get('theme_ceiling')} ｜ "
            f"**NQ上限**: {combined.get('nq_ceiling')} ｜ "
            f"**利率硬上限**: {combined.get('rates_ceiling')} "
            f"(mode={combined.get('rates_bind_mode')})"
        )
        if combined.get("denominator_ceiling_raw") is not None:
            L.append(
                f"- **分母绑定**: tier={combined.get('denom_tier')} ｜ "
                f"raw={combined.get('denominator_ceiling_raw')} → "
                f"held={combined.get('denominator_ceiling_held')} ｜ "
                f"hyst={combined.get('denom_hysteresis')}"
            )
        rs = combined.get("rates_shadow") or {}
        if rs:
            L.append(
                f"- **利率软/诊断**: engaged={rs.get('engaged')} "
                f"cap={rs.get('rates_cap')} reason={rs.get('reason')} "
                f"b_zone={rs.get('b_zone')} (shadow 默认不进 min)"
            )
        cb = combined.get("combined_budget")
        if cb is None:
            L.append("- **合成上限**: `null`（可用工具不足，拒绝给出完整合成）")
        else:
            binding = ", ".join(combined.get("binding") or []) or "—"
            L.append(f"- **合成上限 = min = {cb}** （约束项: {binding}）")
            L.append(
                f"- **完整三工具**: {'是' if combined.get('complete') else '否'} ｜ "
                f"**降级**: {'是' if combined.get('degraded') else '否'} ｜ "
                f"theme_status={combined.get('theme_status')} ｜ "
                f"nq_status={combined.get('nq_status')} ｜ reason={combined.get('reason')}"
            )
        L.append(
            "> 合成上限是观察层最严格天花板参考；主题 stale/missing 时不参与 min。"
            "实盘仍以 kernel `decide()` 为准。"
        )
        
    ppo_blk = (combined or {}).get("positioning_path") if combined else None
    if ppo_blk:
        L.append("## 6. Positioning Path (PPO)")
        L.append("")
        L.append(
            f"- **path**: **{ppo_blk.get('path_zh') or ppo_blk.get('path')}** "
            f"(`{ppo_blk.get('path')}`) | gate: `{ppo_blk.get('gate')}` | "
            f"mode={ppo_blk.get('mode')} | skew={ppo_blk.get('skew')}"
        )
        if ppo_blk.get("soft_cap") is not None:
            L.append(f"- **soft_cap**: {ppo_blk.get('soft_cap')}")
        L.append("")

    rr = (combined or {}).get("re_risk") if combined else None
    if rr:
        L.append("## 7. Re-Risk (Scheme A)")
        L.append("")
        L.append(
            f"- **defense_ceiling**: {rr.get('defense_ceiling')} | "
            f"**target_budget**: **{rr.get('target_budget')}** | "
            f"permit={rr.get('risk_on_permit')} | action={rr.get('action')}"
        )
        blockers = rr.get("permit_blockers") or []
        if blockers:
            L.append(f"- **permit_blockers**: {', '.join(map(str, blockers))}")
        else:
            L.append(
                f"- **permit_streak**: {rr.get('permit_streak')} | "
                f"step={rr.get('step')} | days_since_up={rr.get('days_since_up')}"
            )
        L.append(
            "> Desk primary sizing hint is **target_budget** (slow re-risk). "
            "defense_ceiling remains the hard stress cap."
        )
        L.append("")

    L.append(f"> **{synth.get('tone', '')}**")
    L.append("")
    L.append("---")
    L.append("")
    
    # ⑥ 白话交易建议（Pine 读法）
    if advice is None:
        advice = build_plain_advice(theme, tech, denom, nq, synth, combined)
    L.extend(format_plain_advice_md(advice))

    L.append("*本文件由 `scripts/daily_macro_consolidated.py` 自动汇总生成。*")
    L.append("*三工具交叉验证；任何单一工具都不构成交易信号。*")
    L.append("")
    return "\n".join(L)


def build_json(
    date_str: str,
    denom: Optional[Dict],
    tech: Optional[Dict],
    theme: Optional[Dict],
    synth: Dict[str, Any],
    combined: Optional[Dict[str, Any]] = None,
    *,
    warnings: Optional[List[str]] = None,
    errors: Optional[List[str]] = None,
    sentiment: Optional[Dict[str, Any]] = None,
    nq: Optional[Dict[str, Any]] = None,
    sector: Optional[Dict[str, Any]] = None,
    shock: Optional[Dict[str, Any]] = None,
    rates: Optional[Dict[str, Any]] = None,
    ppo: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    return {
        "report_date": date_str,
        "schema": "macro-os.daily-macro-consolidated.v4",
        "as_of": {
            "theme": synth.get("theme_date"),
            "tech": synth.get("tech_date"),
            "denominator": synth.get("denom_date"),
            "nq_driver": synth.get("nq_date"),
            "sector_rotation": (sector or {}).get("as_of"),
            "shock_absorption": (shock or {}).get("date"),
        },
        "warnings": list(warnings or []),
        "errors": list(errors or []),
        "synthesis": {
            "consistency": synth.get("consistency"),
            "alignment": synth.get("alignment"),
            "bias": synth.get("bias"),
            "data_quality": synth.get("data_quality"),
            "pressure_override": synth.get("pressure_override"),
            "equity_down": synth.get("equity_down"),
            "equity_stress": synth.get("equity_stress"),
            "risk_budget": synth.get("risk_budget"),
            "dampener_active": synth.get("dampener_active"),
            "denom_tag": synth.get("denom_tag"),
            "denom_bias": synth.get("denom_bias"),
            "tech_bias": synth.get("tech_bias"),
            "theme_bias": synth.get("theme_bias"),
            "narrative": synth.get("narrative"),
            "divergences": synth.get("divergences"),
            "tone": synth.get("tone"),
            "theme_quality": synth.get("theme_quality"),
            "nq_bias": synth.get("nq_bias"),
            "nq_veto": synth.get("nq_veto"),
            "nq_state": synth.get("nq_state"),
            "nq_quality": synth.get("nq_quality"),
            "shock_absorption": synth.get("shock_absorption"),
        },
        "combined_risk_budget": combined,
        "rates_stress": rates,
        "positioning_path": ppo,
        "plain_advice": synth.get("plain_advice"),
        "denominator_state": denom,
        "tech_dampener": tech,
        "theme_state_machine": theme,
        "nq_driver": nq,
        "sentiment_shadow": sentiment,
        "sector_rotation": sector,
        "shock_absorption": shock,
    }


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Daily Macro Consolidated Report (denominator + tech dampener + theme). "
            f"Default --report-dir: {DEFAULT_REPORT_DIR}"
        )
    )
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="YYYY-MM-DD")
    parser.add_argument(
        "--report-dir",
        default=str(DEFAULT_REPORT_DIR),
        help=f"artifact dir (default: {DEFAULT_REPORT_DIR})",
    )
    parser.add_argument(
        "--config",
        default=str(DEFAULT_CONFIG),
        help="YAML config for ceilings / stale policy",
    )
    parser.add_argument("--no-run-theme", action="store_true")
    parser.add_argument("--no-run-tech", action="store_true")
    parser.add_argument("--no-run-denom", action="store_true")
    parser.add_argument("--no-run-nq", action="store_true")
    parser.add_argument("--no-run-sector", action="store_true")
    parser.add_argument("--no-run-shock", action="store_true", help="skip 冲击吸收旁证脚本运行")
    parser.add_argument("--force-refresh", action="store_true", help="force re-run all observers")
    parser.add_argument(
        "--allow-stale",
        action="store_true",
        help="allow nearest dated artifact within configured lag when exact date missing",
    )
    parser.add_argument(
        "--include-sentiment",
        action="store_true",
        help="attach sentiment shadow json if present (bypass, observation only)",
    )
    args = parser.parse_args(argv)
    if args.force_refresh:
        args.no_run_theme = False
        args.no_run_tech = False
        args.no_run_denom = False
        args.no_run_nq = False
        args.no_run_sector = False

    cfg = load_config(Path(args.config))
    max_lag = int(cfg.get("stale_fallback_max_trading_days", cfg.get("stale_fallback_max_calendar_days", 1)))
    warnings: List[str] = []
    errors: List[str] = []

    report_dir = Path(args.report_dir)
    report_dir.mkdir(parents=True, exist_ok=True)
    date_str = args.date
    logger.info(
        "Date: %s | Report dir: %s | allow_stale=%s",
        date_str,
        report_dir,
        args.allow_stale,
    )

    denom = ensure_denominator(
        report_dir,
        date_str,
        args.no_run_denom,
        allow_stale=args.allow_stale,
        max_lag_days=max_lag,
        warnings=warnings,
        errors=errors,
    )
    tech = ensure_tech(
        report_dir,
        date_str,
        args.no_run_tech,
        warnings=warnings,
        errors=errors,
    )
    theme = ensure_theme(
        report_dir,
        date_str,
        args.no_run_theme,
        allow_stale=args.allow_stale,
        max_lag_days=max_lag,
        warnings=warnings,
        errors=errors,
    )
    nq = ensure_nq(
        report_dir,
        date_str,
        args.no_run_nq,
        allow_stale=args.allow_stale,
        max_lag_days=max_lag,
        warnings=warnings,
        errors=errors,
    )
    sector = ensure_sector(
        report_dir,
        date_str,
        args.no_run_sector,
        allow_stale=args.allow_stale,
        max_lag_days=max_lag,
        warnings=warnings,
        errors=errors,
    )
    shock = ensure_shock(
        report_dir,
        date_str,
        args.no_run_shock,
        allow_stale=args.allow_stale,
        max_lag_days=max_lag,
        warnings=warnings,
        errors=errors,
    )

    denom_date = (denom or {}).get("date")
    tech_date = (tech or {}).get("date", date_str)
    theme_date = (theme or {}).get("date", date_str)
    nq_date = (nq or {}).get("date", date_str)

    synth = synthesize(
        theme,
        tech,
        denom,
        theme_date,
        tech_date,
        denom_date,
        report_date=date_str,
        cfg=cfg,
        nq=nq,
        nq_date=nq_date,
        shock=shock,
    )
    rates = build_rates_stress_from_denom(denom, cfg=cfg, warnings=warnings)
    prev_combined = _load_prev_combined(report_dir, date_str)
    ppo = build_positioning_path(
        denom,
        rates,
        nq,
        cfg=cfg,
        prev_combined=prev_combined,
        report_dir=report_dir,
        warnings=warnings,
    )
    combined = compute_combined_budget(
        denom,
        tech,
        theme,
        nq,
        rates,
        ppo,
        data_quality=str(synth.get("data_quality") or "ok"),
        nq_quality=str(synth.get("nq_quality") or "ok"),
        cfg=cfg,
        prev_combined=prev_combined,
    )
    re_risk = build_re_risk_snapshot(
        combined, ppo, cfg=cfg, prev_combined=prev_combined
    )
    if isinstance(combined, dict) and re_risk is not None:
        combined = dict(combined)
        combined["re_risk"] = re_risk
        combined["defense_ceiling"] = re_risk.get("defense_ceiling")
        combined["target_budget"] = re_risk.get("target_budget")
        combined["risk_on_permit"] = re_risk.get("risk_on_permit")
    sentiment = (
        try_load_sentiment_shadow(report_dir, date_str, warnings)
        if args.include_sentiment
        else None
    )

    advice = build_plain_advice(theme, tech, denom, nq, synth, combined)
    synth = dict(synth)
    synth["plain_advice"] = advice

    md = build_report(
        date_str,
        denom,
        tech,
        theme,
        synth,
        combined,
        warnings=warnings,
        sentiment=sentiment,
        nq=nq,
        sector=sector,
        shock=shock,
        advice=advice,
    )
    payload = build_json(
        date_str,
        denom,
        tech,
        theme,
        synth,
        combined,
        warnings=warnings,
        errors=errors,
        sentiment=sentiment,
        nq=nq,
        sector=sector,
        shock=shock,
        rates=rates,
        ppo=ppo,
    )

    out_md = report_dir / f"daily_macro_{date_str}.md"
    out_json = report_dir / f"daily_macro_{date_str}.json"
    out_md.write_text(md, encoding="utf-8")
    out_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    logger.info("written: %s", out_md)
    logger.info("written: %s", out_json)

    try:
        print(md)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(md.encode("utf-8", errors="replace"))
        sys.stdout.buffer.write(b"\n")
    print(f"\n[written] {out_md}")
    print(f"[written] {out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
