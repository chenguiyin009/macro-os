#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文章思路观测旁证 · 日频（Article Macro Watch · daily headless）

--------------------------------------------------------------------------------
WHY THIS EXISTS
--------------------------------------------------------------------------------
作者叙事线旁证：把「一年累计加息定价 / WTI 绝对价 / 10Y / 盈亏平衡通胀相对均值 /
MOVE」收成一份只读观测 artifact，挂到晨报旁证小节。

定位（对齐冲击吸收旁证）：
  - 只观察 + 报警，不进主状态机
  - **永不**进入 `compute_combined_budget` / 不放松或覆盖分母 `tight→0.35`
  - **不做第六腿**

Fed 一年累计加息定价 bp：
  开源自算 CME 30-Day Fed Funds 结算（优先 `cme-fedwatch`：CME 结算 + FRED
  EFFR/target + 内置 FOMC 日程）。算法版本 `cme_settlement_cum_v1`：
    取 as_of 起 365 日内最远一场 FOMC，对其概率分布取目标区间中点期望，
    减去当前目标区间中点，×100 → bp。
  与 CME FedWatch / QuikStrike 官网可能有偏差（日结算 vs 盘中、日程硬编码、
  月末敏感）；接受偏差，失败则整段 Fed degraded，**禁止编造 bp**。

WTI / 10Y / T10YIE：复用 yfinance + FRED 公开 CSV，少加依赖。
MOVE：TV-only，无开源代理 → null + abstain_reason（与冲击吸收同口径）。

OUTPUT: output/article_macro_watch_<as_of>.{md,json}
门槛文案（作者叙事线，未标定）：Fed 70/100/110 bp；油 90。
--------------------------------------------------------------------------------
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")
os.environ.setdefault("MKL_NUM_THREADS", "1")
os.environ.setdefault("NUMEXPR_MAX_THREADS", "1")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = REPO_ROOT / "output"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("article-macro-watch")

DEFAULT_PROXY = "http://127.0.0.1:7890"
_PROXY_ENV_KEYS = ("HTTPS_PROXY", "HTTP_PROXY")
_PROXY_OFF_VALUES = ("", "off", "none", "0", "false", "no")

ALGO_VERSION = "cme_settlement_cum_v1"
HORIZON_DAYS = 365

# 作者叙事线门槛（观察文案，不进合成 min）
THRESHOLDS = {
    "fed_1y_hike_bp": {"warn": 70, "elevated": 100, "extreme": 110},
    "wti_narrative_line": 90.0,
    "t10yie_mean_lookback_calendar_days": 1095,  # ~3y
}


def _ensure_proxy() -> None:
    if any(os.environ.get(k) for k in _PROXY_ENV_KEYS):
        return
    sp = os.environ.get("SECTOR_PROXY")
    if sp is not None and str(sp).strip().lower() in _PROXY_OFF_VALUES:
        return
    try:
        proxy = sp if (sp and str(sp).strip()) else DEFAULT_PROXY
        os.environ.setdefault("HTTPS_PROXY", proxy)
        os.environ.setdefault("HTTP_PROXY", proxy)
    except Exception:
        pass


def _range_midpoint(label: str) -> Optional[float]:
    """Parse '3.75%-4.00%' → 3.875. Returns None on bad label."""
    try:
        s = str(label).strip().replace("%", "")
        a, b = s.split("-", 1)
        return (float(a) + float(b)) / 2.0
    except Exception:
        return None


def _expected_rate_from_probs(probabilities: Dict[str, float]) -> Optional[float]:
    if not probabilities:
        return None
    total_w = 0.0
    acc = 0.0
    for label, pct in probabilities.items():
        mid = _range_midpoint(label)
        if mid is None:
            continue
        w = float(pct) / 100.0
        acc += mid * w
        total_w += w
    if total_w <= 0:
        return None
    return acc / total_w


def compute_fed_1y_hike_bp(
    *,
    as_of: dt.date,
    trade_date: Optional[dt.date] = None,
) -> Dict[str, Any]:
    """Open-source CME settlement FedWatch → 1y cumulative hike bp.

    On any hard failure returns degraded payload with fed_1y_hike_bp=None
    (never fabricates a number).
    """
    base: Dict[str, Any] = {
        "fed_1y_hike_bp": None,
        "meeting_anchor": None,
        "meetings_in_horizon": [],
        "algo_version": ALGO_VERSION,
        "source": "cme-fedwatch+CME_settlement+FRED_EFFR",
        "quality": "degraded",
        "degraded": True,
        "degraded_reason": None,
        "effr": None,
        "current_target": None,
        "trade_date": None,
        "expected_rate_1y": None,
        "current_mid": None,
        "disclaimer": (
            "开源自算 CME 日结算 FedWatch；与官网 QuikStrike/盘中可能偏差，"
            "接受偏差；失败不编造 bp。"
        ),
    }
    try:
        from cme_fedwatch import get_probabilities
    except Exception as exc:  # noqa: BLE001
        base["degraded_reason"] = f"cme-fedwatch import failed: {exc}"
        logger.warning(base["degraded_reason"])
        return base

    try:
        kwargs: Dict[str, Any] = {}
        if trade_date is not None:
            kwargs["trade_date"] = trade_date
        data = get_probabilities(**kwargs)  # all upcoming meetings
    except Exception as exc:  # noqa: BLE001
        base["degraded_reason"] = f"get_probabilities failed: {exc}"
        logger.warning(base["degraded_reason"])
        return base

    if not isinstance(data, dict) or not data.get("meetings"):
        base["degraded_reason"] = "empty meetings from cme-fedwatch"
        return base

    cur_label = data.get("current_target")
    cur_mid = _range_midpoint(cur_label) if cur_label else None
    if cur_mid is None:
        base["degraded_reason"] = f"unparseable current_target: {cur_label!r}"
        base["current_target"] = cur_label
        return base

    td_raw = data.get("trade_date")
    try:
        td = dt.date.fromisoformat(str(td_raw)[:10]) if td_raw else None
    except Exception:
        td = None

    horizon_end = as_of + dt.timedelta(days=HORIZON_DAYS)
    in_h: List[Dict[str, Any]] = []
    for m in data["meetings"]:
        try:
            md = dt.date.fromisoformat(str(m.get("date"))[:10])
        except Exception:
            continue
        if md > horizon_end:
            continue
        if md < as_of:
            continue
        exp = _expected_rate_from_probs(m.get("probabilities") or {})
        if exp is None:
            continue
        in_h.append(
            {
                "date": md.isoformat(),
                "contract": m.get("contract"),
                "expected_rate": round(float(exp), 4),
                "cum_hike_bp": round((float(exp) - cur_mid) * 100.0, 1),
            }
        )

    if not in_h:
        base["degraded_reason"] = (
            f"no FOMC meetings in [{as_of.isoformat()}, {horizon_end.isoformat()}]"
        )
        base["current_target"] = cur_label
        base["current_mid"] = cur_mid
        base["effr"] = data.get("effr")
        base["trade_date"] = td.isoformat() if td else None
        return base

    anchor = in_h[-1]
    bp = float(anchor["cum_hike_bp"])
    base.update(
        {
            "fed_1y_hike_bp": bp,
            "meeting_anchor": {
                "date": anchor["date"],
                "contract": anchor.get("contract"),
                "horizon_days": HORIZON_DAYS,
            },
            "meetings_in_horizon": in_h,
            "quality": "ok",
            "degraded": False,
            "degraded_reason": None,
            "effr": data.get("effr"),
            "current_target": cur_label,
            "current_mid": round(cur_mid, 4),
            "expected_rate_1y": anchor["expected_rate"],
            "trade_date": td.isoformat() if td else None,
            "target_source": data.get("target_source"),
            "schedule_status": data.get("schedule_status"),
        }
    )
    return base


def _download_close(ticker: str, period: str = "1y") -> Optional[Any]:
    import pandas as pd
    import yfinance as yf

    _ensure_proxy()
    try:
        raw = yf.download(
            ticker, period=period, auto_adjust=True, progress=False, threads=False
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("yfinance download failed %s: %s", ticker, exc)
        return None
    if raw is None or getattr(raw, "empty", True):
        logger.warning("yfinance empty: %s", ticker)
        return None
    cols = raw.columns
    close = None
    if getattr(cols, "nlevels", 1) > 1:
        if ("Close", ticker) in cols:
            close = raw[("Close", ticker)]
        elif (ticker, "Close") in cols:
            close = raw[(ticker, "Close")]
        elif "Close" in cols.get_level_values(0):
            cd = raw.xs("Close", axis=1, level=0)
            close = cd[ticker] if ticker in cd.columns else cd.iloc[:, 0]
        else:
            return None
    else:
        close = raw["Close"] if "Close" in cols else None
    if close is None:
        return None
    s = pd.to_numeric(close, errors="coerce").dropna()
    s.index = pd.to_datetime(s.index).normalize()
    return s if len(s) >= 5 else None


def _clamp_series_last(series: Any, as_of: dt.date) -> Tuple[Optional[float], Optional[str]]:
    if series is None or len(series) == 0:
        return None, None
    idx = series.index
    try:
        mask = idx.date <= as_of if hasattr(idx, "date") else idx <= pd_Timestamp(as_of)
    except Exception:
        # fallback: compare iso strings
        keep = [i for i in idx if str(i)[:10] <= as_of.isoformat()]
        if not keep:
            return None, None
        last_i = keep[-1]
        return float(series.loc[last_i]), str(last_i)[:10]
    sub = series.loc[mask]
    if sub is None or len(sub) == 0:
        return None, None
    last_i = sub.index[-1]
    return float(sub.iloc[-1]), str(last_i)[:10]


def pd_Timestamp(d: dt.date):
    import pandas as pd
    return pd.Timestamp(d)


def fetch_wti(as_of: dt.date) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "wti_last": None,
        "as_of": None,
        "ticker": "CL=F",
        "source": "yfinance",
        "quality": "degraded",
        "narrative_line": THRESHOLDS["wti_narrative_line"],
        "note": "作者叙事线绝对价门槛 90（观察文案，不进合成 min）",
    }
    s = _download_close("CL=F", period="6mo")
    val, d = _clamp_series_last(s, as_of)
    if val is None:
        out["degraded_reason"] = "WTI CL=F unavailable"
        return out
    out.update(
        {
            "wti_last": round(val, 2),
            "as_of": d,
            "quality": "ok",
            "above_narrative_line": bool(val >= THRESHOLDS["wti_narrative_line"]),
        }
    )
    return out


def fetch_dgs10(as_of: dt.date) -> Dict[str, Any]:
    """Prefer FRED DGS10; fall back to yfinance ^TNX."""
    out: Dict[str, Any] = {
        "dgs10": None,
        "tnx": None,
        "as_of": None,
        "source": None,
        "quality": "degraded",
        "unit": "percent",
    }
    # FRED
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from adapters.fred import fetch_fred_series

        pts = fetch_fred_series("DGS10", lookback_days=400, timeout_seconds=20)
        pts = [p for p in pts if p.date <= as_of.isoformat()]
        if pts:
            last = pts[-1]
            out.update(
                {
                    "dgs10": round(float(last.value), 3),
                    "as_of": last.date,
                    "source": "FRED:DGS10",
                    "quality": "ok",
                }
            )
            return out
    except Exception as exc:  # noqa: BLE001
        logger.warning("FRED DGS10 failed: %s", exc)

    s = _download_close("^TNX", period="6mo")
    val, d = _clamp_series_last(s, as_of)
    if val is None:
        out["degraded_reason"] = "DGS10/TNX unavailable"
        return out
    out.update(
        {
            "dgs10": round(val, 3),
            "tnx": round(val, 3),
            "as_of": d,
            "source": "yfinance:^TNX",
            "quality": "ok",
            "note": "FRED DGS10 missing; using ^TNX proxy",
        }
    )
    return out


def fetch_t10yie(as_of: dt.date) -> Dict[str, Any]:
    lookback = int(THRESHOLDS["t10yie_mean_lookback_calendar_days"])
    out: Dict[str, Any] = {
        "t10yie": None,
        "as_of": None,
        "mean": None,
        "mean_lookback_calendar_days": lookback,
        "deviation": None,
        "deviation_bp": None,
        "source": "FRED:T10YIE",
        "quality": "degraded",
        "unit": "percent",
    }
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from adapters.fred import fetch_fred_series

        pts = fetch_fred_series(
            "T10YIE", lookback_days=lookback + 60, timeout_seconds=25
        )
        pts = [p for p in pts if p.date <= as_of.isoformat()]
        if not pts:
            out["degraded_reason"] = "T10YIE empty"
            return out
        start = (as_of - dt.timedelta(days=lookback)).isoformat()
        window = [p for p in pts if p.date >= start] or pts
        vals = [float(p.value) for p in window]
        mean = sum(vals) / len(vals)
        last = pts[-1]
        last_v = float(last.value)
        dev = last_v - mean
        out.update(
            {
                "t10yie": round(last_v, 3),
                "as_of": last.date,
                "mean": round(mean, 3),
                "n_obs": len(vals),
                "deviation": round(dev, 3),
                "deviation_bp": round(dev * 100.0, 1),
                "quality": "ok",
            }
        )
        return out
    except Exception as exc:  # noqa: BLE001
        out["degraded_reason"] = f"T10YIE fetch failed: {exc}"
        logger.warning(out["degraded_reason"])
        return out


def move_abstain() -> Dict[str, Any]:
    return {
        "move": None,
        "quality": "abstain",
        "abstain_reason": (
            "MOVE (TVC:MOVE) 为 TV-only，无可靠开源日频代理；"
            "按冲击吸收同口径弃权，不参与任何计算、不编造。"
        ),
    }


def fed_alert_level(bp: Optional[float]) -> str:
    if bp is None:
        return "unavailable"
    t = THRESHOLDS["fed_1y_hike_bp"]
    if bp >= t["extreme"]:
        return "extreme"
    if bp >= t["elevated"]:
        return "elevated"
    if bp >= t["warn"]:
        return "warn"
    return "calm"


def build_payload(as_of: dt.date) -> Dict[str, Any]:
    fed = compute_fed_1y_hike_bp(as_of=as_of)
    wti = fetch_wti(as_of)
    dgs = fetch_dgs10(as_of)
    bei = fetch_t10yie(as_of)
    move = move_abstain()

    legs_ok = [
        fed.get("quality") == "ok",
        wti.get("quality") == "ok",
        dgs.get("quality") == "ok",
        bei.get("quality") == "ok",
    ]
    # MOVE always abstains — does not degrade whole artifact alone
    if all(legs_ok):
        quality = "ok"
        degraded = False
        degraded_reason = None
    elif any(legs_ok):
        quality = "partial"
        degraded = True
        reasons = []
        if fed.get("quality") != "ok":
            reasons.append(f"fed:{fed.get('degraded_reason') or fed.get('quality')}")
        if wti.get("quality") != "ok":
            reasons.append(f"wti:{wti.get('degraded_reason') or 'bad'}")
        if dgs.get("quality") != "ok":
            reasons.append(f"dgs10:{dgs.get('degraded_reason') or 'bad'}")
        if bei.get("quality") != "ok":
            reasons.append(f"t10yie:{bei.get('degraded_reason') or 'bad'}")
        degraded_reason = "; ".join(reasons)
    else:
        quality = "degraded"
        degraded = True
        degraded_reason = "all priced legs failed"

    thr = {
        "fed_1y_hike_bp_warn": THRESHOLDS["fed_1y_hike_bp"]["warn"],
        "fed_1y_hike_bp_elevated": THRESHOLDS["fed_1y_hike_bp"]["elevated"],
        "fed_1y_hike_bp_extreme": THRESHOLDS["fed_1y_hike_bp"]["extreme"],
        "wti_narrative_line": THRESHOLDS["wti_narrative_line"],
        "copy": (
            f"Fed 一年累计加息定价门槛（观察）："
            f"{THRESHOLDS['fed_1y_hike_bp']['warn']}/"
            f"{THRESHOLDS['fed_1y_hike_bp']['elevated']}/"
            f"{THRESHOLDS['fed_1y_hike_bp']['extreme']} bp；"
            f"WTI 作者叙事线绝对价 {THRESHOLDS['wti_narrative_line']}。"
            "本旁证不进合成 min / 不改 denom_policy。"
        ),
    }

    alerts: List[str] = []
    bp = fed.get("fed_1y_hike_bp")
    lvl = fed_alert_level(bp if isinstance(bp, (int, float)) else None)
    if lvl == "extreme":
        alerts.append(f"Fed 1y 加息定价 {bp}bp ≥ 极端线 {thr['fed_1y_hike_bp_extreme']}")
    elif lvl == "elevated":
        alerts.append(f"Fed 1y 加息定价 {bp}bp ≥ 抬升线 {thr['fed_1y_hike_bp_elevated']}")
    elif lvl == "warn":
        alerts.append(f"Fed 1y 加息定价 {bp}bp ≥ 观察线 {thr['fed_1y_hike_bp_warn']}")
    if wti.get("above_narrative_line"):
        alerts.append(
            f"WTI {wti.get('wti_last')} ≥ 叙事线 {thr['wti_narrative_line']}"
        )
    if fed.get("degraded"):
        alerts.append(f"Fed 段 degraded：{fed.get('degraded_reason')}")

    return {
        "date": as_of.isoformat(),
        "as_of": as_of.isoformat(),
        "generated_at": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "script": "article_macro_watch_daily.py",
        "schema": "macro-os.article-macro-watch.v1",
        "role": "article_narrative_crosscheck",
        "bound_to_combined_min": False,
        "note": (
            "思路观测旁证：只观察+报警；不进 compute_combined_budget；"
            "不放松/不覆盖分母 tight→0.35；不做第六腿。"
        ),
        "quality": quality,
        "degraded": degraded,
        "degraded_reason": degraded_reason,
        "fed_1y_hike_bp": fed.get("fed_1y_hike_bp"),
        "fed": fed,
        "wti_last": wti.get("wti_last"),
        "wti": wti,
        "dgs10": dgs.get("dgs10"),
        "tnx": dgs.get("tnx"),
        "rates_10y": dgs,
        "t10yie": bei.get("t10yie"),
        "t10yie_detail": bei,
        "t10yie_vs_mean": {
            "last": bei.get("t10yie"),
            "mean": bei.get("mean"),
            "deviation": bei.get("deviation"),
            "deviation_bp": bei.get("deviation_bp"),
            "lookback_calendar_days": bei.get("mean_lookback_calendar_days"),
        },
        "move": move.get("move"),
        "move_detail": move,
        "abstain_reason": move.get("abstain_reason"),
        "thresholds": thr,
        "fed_alert_level": lvl,
        "alerts": alerts,
    }


def build_report(p: Dict[str, Any]) -> str:
    L: List[str] = []
    L.append(f"# 文章思路观测旁证 · {p['as_of']}")
    L.append("")
    L.append(
        "> **观察旁证 · 非交易指令**。不进合成预算 min，不改 denom_policy，不做第六腿。"
    )
    L.append("")
    L.append(f"- **质量**: {p.get('quality')} ｜ degraded={p.get('degraded')}")
    if p.get("degraded_reason"):
        L.append(f"- **降级原因**: {p['degraded_reason']}")
    L.append("")
    L.append("## Fed 一年累计加息定价")
    L.append("")
    fed = p.get("fed") or {}
    if fed.get("fed_1y_hike_bp") is not None:
        L.append(
            f"- **fed_1y_hike_bp**: **{fed['fed_1y_hike_bp']} bp** "
            f"（alert={p.get('fed_alert_level')}）"
        )
        anc = fed.get("meeting_anchor") or {}
        L.append(
            f"- **meeting 锚点**: {anc.get('date')} / {anc.get('contract')} "
            f"（horizon={anc.get('horizon_days')}d）"
        )
        L.append(
            f"- **当前目标**: {fed.get('current_target')} ｜ EFFR={fed.get('effr')} ｜ "
            f"结算日={fed.get('trade_date')}"
        )
        L.append(
            f"- **E[rate@1y]**: {fed.get('expected_rate_1y')} ｜ mid={fed.get('current_mid')}"
        )
    else:
        L.append(f"- **fed_1y_hike_bp**: null（degraded）— {fed.get('degraded_reason')}")
    L.append(
        f"- **算法**: `{fed.get('algo_version')}` ｜ source=`{fed.get('source')}`"
    )
    L.append(f"- **偏差声明**: {fed.get('disclaimer')}")
    L.append("")
    L.append("## WTI / 10Y / BEI / MOVE")
    L.append("")
    wti = p.get("wti") or {}
    L.append(
        f"- **WTI**: {p.get('wti_last')}（as_of={wti.get('as_of')}，"
        f"叙事线 {THRESHOLDS['wti_narrative_line']}，"
        f"{'触及/越过' if wti.get('above_narrative_line') else '未过线'}）"
    )
    L.append(
        f"- **DGS10/10Y**: {p.get('dgs10')}%（source={ (p.get('rates_10y') or {}).get('source') }，"
        f"as_of={(p.get('rates_10y') or {}).get('as_of')}）"
    )
    vs = p.get("t10yie_vs_mean") or {}
    L.append(
        f"- **T10YIE**: {p.get('t10yie')}% ｜ 近年均值 {vs.get('mean')} "
        f"（lookback {vs.get('lookback_calendar_days')}d）｜ "
        f"偏离 {vs.get('deviation')} pp（{vs.get('deviation_bp')} bp）"
    )
    L.append(f"- **MOVE**: null — {(p.get('move_detail') or {}).get('abstain_reason')}")
    L.append("")
    L.append("## 门槛文案")
    L.append("")
    L.append(f"- {(p.get('thresholds') or {}).get('copy')}")
    L.append("")
    if p.get("alerts"):
        L.append("## 报警")
        L.append("")
        for a in p["alerts"]:
            L.append(f"- ⚠ {a}")
        L.append("")
    L.append(
        "> 完整逻辑见 `scripts/article_macro_watch_daily.py`。"
        "P2：永不进 combined min / 不改 tight→0.35 / 不做第六腿。"
    )
    return "\n".join(L) + "\n"


def write_outputs(report_dir: Path, p: Dict[str, Any]) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = f"article_macro_watch_{p['as_of']}"
    (report_dir / f"{stem}.json").write_text(
        json.dumps(p, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (report_dir / f"{stem}.md").write_text(build_report(p), encoding="utf-8")
    logger.info("written %s.{md,json}", stem)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(
        description="Article macro watch daily (narrative cross-check, not in combined min)"
    )
    ap.add_argument(
        "--date",
        default=None,
        help="as_of YYYY-MM-DD (US session clamp); default=today UTC date",
    )
    ap.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    args = ap.parse_args(argv)

    if args.date:
        try:
            as_of = dt.date.fromisoformat(str(args.date)[:10])
        except ValueError:
            logger.error("invalid --date %r", args.date)
            return 2
    else:
        as_of = dt.datetime.now(dt.timezone.utc).date()

    try:
        payload = build_payload(as_of)
    except Exception as exc:  # noqa: BLE001
        logger.error("build_payload failed: %s", exc)
        return 1

    write_outputs(Path(args.report_dir), payload)
    try:
        print(build_report(payload))
    except Exception:
        pass
    print(
        f"\n[written] article_macro_watch_{payload['as_of']}.json / .md",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
