# -*- coding: utf-8 -*-
"""
paper_abc_ledger.py — A/B/C 三策略纸面净值累计对比
=================================================
读取 rotation_results/daily_abc_YYYY-MM-DD.json 的目标权重，
用 yfinance 日线复权价滚动计价，维护独立账本：

  tech_A / tech_B / tech_C
  spy_A  / spy_B  / spy_C
  bh_QQQ / bh_SPY（对照）

规则（简单可审计）：
  - 每条 sleeve 本金 PRINCIPAL，从首个快照日起算
  - 相邻交易日：按「上一快照权重」吃当日收益（含显式 BIL 短债腿，非闲置 0%）
  - 若当日有新快照：收盘按新权重再平衡，换手扣 COST 单边
  - 无快照的交易日继续沿用最近权重
  - 缺价标的（尤其 BIL）不得静默当 0%：记入 missing_price_warnings
  - 另算 tech_C / spy_C 的「现金=0 影子」：同权重，仅把 BIL 段日收益强制为 0，用于对照短债贡献

输出：
  rotation_results/paper_abc_ledger.json
  rotation_results/paper_abc_nav_YYYY-MM-DD.md  （累计对比表）
  可选：把表格块 upsert 进 daily_instruction / rotation / sector md

用法：
  python paper_abc_ledger.py
  python paper_abc_ledger.py --abc rotation_results/daily_abc_2026-08-24.json
  python paper_abc_ledger.py --append-to rotation_results/daily_instruction_2026-08-24.md
"""
from __future__ import annotations

import argparse
import logging
import glob
import json
import os
import re
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yfinance as yf

HERE = os.path.dirname(os.path.abspath(__file__))
RES = os.path.join(HERE, "rotation_results")
LEDGER_PATH = os.path.join(RES, "paper_abc_ledger.json")
PRINCIPAL = 100_000.0
COST = 0.0010  # 单边
TZ = timezone(timedelta(hours=8))
logger = logging.getLogger("paper_abc_ledger")

HDR = "## 纸面净值累计对比（A/B/C）"
SLEEVES = [
    "tech_A", "tech_B", "tech_C",
    "spy_A", "spy_B", "spy_C",
    "bh_QQQ", "bh_SPY",
]
LABELS = {
    "tech_A": "科技·A研究最优",
    "tech_B": "科技·B折中对照",
    "tech_C": "科技·C稳健生产",
    "spy_A": "标普·A研究最优",
    "spy_B": "标普·B折中对照",
    "spy_C": "标普·C稳健生产",
    "bh_QQQ": "对照·全仓QQQ",
    "bh_SPY": "对照·全仓SPY",
}


def _load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _save_json(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def discover_abc_files():
    pats = [
        os.path.join(RES, "daily_abc_*.json"),
        os.path.join(HERE, "daily_abc_*.json"),
    ]
    files = []
    for p in pats:
        files.extend(glob.glob(p))
    # unique by basename keep newest path
    by = {}
    for f in files:
        by[os.path.basename(f)] = f
    out = []
    for bn, f in sorted(by.items()):
        m = re.search(r"daily_abc_(\d{4}-\d{2}-\d{2})\.json", bn)
        if m:
            out.append((m.group(1), f))
    return out


def weights_from_strategy(st: dict) -> dict:
    w = {}
    for row in st.get("weights") or []:
        t = row.get("ticker")
        if not t or t == "?":
            continue
        w[t] = w.get(t, 0.0) + float(row.get("pct") or 0.0) / 100.0
    s = sum(w.values())
    if s <= 0:
        return {"BIL": 1.0}
    return {k: v / s for k, v in w.items()}


def snapshot_targets(abc: dict) -> dict:
    """返回 sleeve -> weight dict。"""
    out = {}
    for side, key in (("tech", "tech"), ("spy", "spy")):
        block = abc.get(key) or {}
        for st in block.get("strategies") or []:
            sid = st.get("id")
            if sid in ("A", "B", "C"):
                out[f"{side}_{sid}"] = weights_from_strategy(st)
    out["bh_QQQ"] = {"QQQ": 1.0}
    out["bh_SPY"] = {"SPY": 1.0}
    return out


def empty_ledger():
    return {
        "schema": "paper_abc_ledger_v1",
        "principal": PRINCIPAL,
        "cost_one_way": COST,
        "created": datetime.now(TZ).strftime("%Y-%m-%d %H:%M"),
        "updated": None,
        "start_date": None,
        "last_date": None,
        "sleeves": {
            s: {
                "nav": PRINCIPAL,
                "peak": PRINCIPAL,
                "max_dd": 0.0,
                "weights": {},
                "history": [],  # {date, nav, day_ret, turnover, bear?}
            }
            for s in SLEEVES
        },
        # 同信号、BIL 段收益强制为 0 的影子账（量化短债贡献，避免「空仓=0%」误解）
        "shadow_cash0": {
            s: {
                "nav": PRINCIPAL,
                "peak": PRINCIPAL,
                "max_dd": 0.0,
                "history": [],
            }
            for s in ("tech_C", "spy_C")
        },
        "missing_price_warnings": [],  # [{date, sleeve, ticker, weight}]
        "applied_snapshots": [],  # dates
    }


def load_ledger(path=LEDGER_PATH):
    if os.path.isfile(path):
        try:
            return _load_json(path)
        except Exception:
            pass
    return empty_ledger()


def universe_tickers(targets_by_date: dict) -> list:
    tickers = {"QQQ", "SPY", "BIL"}
    for td in targets_by_date.values():
        for w in td.values():
            tickers.update(w.keys())
    return sorted(tickers)


def download_prices(tickers, start, end):
    # buffer
    start_d = (pd.Timestamp(start) - pd.Timedelta(days=10)).strftime("%Y-%m-%d")
    end_d = (pd.Timestamp(end) + pd.Timedelta(days=3)).strftime("%Y-%m-%d")
    raw = yf.download(
        tickers, start=start_d, end=end_d, auto_adjust=True,
        progress=False, threads=False, group_by="column",
    )
    if isinstance(raw.columns, pd.MultiIndex):
        cl = raw["Close"] if "Close" in raw.columns.get_level_values(0) else raw["Adj Close"]
    else:
        cl = raw
    if isinstance(cl, pd.Series):
        cl = cl.to_frame(tickers[0] if len(tickers) == 1 else "X")
    cl = cl.reindex(columns=tickers)
    cl = cl.dropna(how="all").ffill()
    if "BIL" in tickers:
        if "BIL" not in cl.columns or cl["BIL"].isna().all():
            raise RuntimeError(
                "BIL price series missing/empty after download — refusing to price cash leg as 0%"
            )
        n_nan = int(cl["BIL"].isna().sum())
        if n_nan:
            logger.warning("BIL still has %d NaN after ffill; those days will warn in day_port_ret", n_nan)
    return cl


def l1_turn(w0: dict, w1: dict) -> float:
    keys = set(w0) | set(w1)
    return float(sum(abs(w1.get(k, 0.0) - w0.get(k, 0.0)) for k in keys))


def day_port_ret(
    weights: dict,
    rets: pd.Series,
    *,
    cash_tickers: tuple = ("BIL",),
    cash_ret_mode: str = "live",  # live | zero
    missing_out: list | None = None,
    date: str | None = None,
    sleeve: str | None = None,
) -> float:
    """组合日收益。cash_ret_mode=zero 时把短债腿收益强制为 0（影子对照）。

    缺价且权重>0 时写入 missing_out（不再静默当 0% 而不告警）。
    """
    r = 0.0
    for t, w in weights.items():
        w = float(w or 0.0)
        if abs(w) < 1e-12:
            continue
        if t in cash_tickers and cash_ret_mode == "zero":
            continue  # 强制 0 贡献
        if t in rets.index and pd.notna(rets[t]):
            r += w * float(rets[t])
        else:
            if missing_out is not None:
                missing_out.append({
                    "date": date, "sleeve": sleeve, "ticker": t, "weight": round(w, 6),
                })
            # 仍按 0 贡献继续滚动，但已告警；BIL 缺价尤其危险
            if t in cash_tickers:
                logger.warning(
                    "missing price for cash leg %s on %s sleeve=%s w=%.4f — contrib treated 0 (WARN)",
                    t, date, sleeve, w,
                )
    return r


def rebuild_from_snapshots(ledger, snapshots: list[tuple[str, dict]], price: pd.DataFrame):
    """用全部快照 + 价格重放净值（幂等）。"""
    if not snapshots:
        return ledger

    # map date -> targets
    by_date = {}
    for d, abc in snapshots:
        by_date[d] = snapshot_targets(abc)

    snap_dates = sorted(by_date.keys())
    # price index as timestamps normalize to date str
    px = price.copy()
    px.index = pd.to_datetime(px.index).tz_localize(None)
    last_px = px.index[-1].strftime("%Y-%m-%d")
    # 若首个快照日尚无收盘价（盘前/周末），对齐到最近已有交易日，便于立刻滚动
    start = snap_dates[0]
    if start > last_px:
        start = last_px
        # 把最新快照权重视为从 last_px 起生效
        by_date[start] = by_date[snap_dates[-1]]
    end = last_px
    days = sorted({d.strftime("%Y-%m-%d") for d in px.index if d.strftime("%Y-%m-%d") >= start})

    # reset sleeves
    new = empty_ledger()
    new["start_date"] = start
    new["applied_snapshots"] = snap_dates

    # current weights per sleeve
    cur_w = {s: {} for s in SLEEVES}
    # init on first snapshot date at close: set weights, nav=principal
    first_t = by_date[snap_dates[0]]
    for s in SLEEVES:
        cur_w[s] = dict(first_t.get(s) or {"BIL": 1.0})
        new["sleeves"][s]["weights"] = dict(cur_w[s])
        new["sleeves"][s]["nav"] = PRINCIPAL
        new["sleeves"][s]["peak"] = PRINCIPAL
        new["sleeves"][s]["history"] = [{
            "date": start,
            "nav": PRINCIPAL,
            "day_ret": 0.0,
            "turnover": 0.0,
            "rebalanced": True,
            "note": "起始建仓",
        }]
    for s in ("tech_C", "spy_C"):
        new["shadow_cash0"][s]["nav"] = PRINCIPAL
        new["shadow_cash0"][s]["peak"] = PRINCIPAL
        new["shadow_cash0"][s]["max_dd"] = 0.0
        new["shadow_cash0"][s]["history"] = [{
            "date": start, "nav": PRINCIPAL, "day_ret": 0.0, "note": "起始建仓(现金=0影子)",
        }]
    missing: list = []

    rets = px.pct_change()
    # date -> timestamp map
    ts_map = {d.strftime("%Y-%m-%d"): d for d in rets.index}
    # walk days after start
    for ds in days:
        if ds <= start:
            continue
        ts = ts_map.get(ds)
        if ts is None:
            continue
        row_ret = rets.loc[ts]

        for s in SLEEVES:
            prev_w = dict(cur_w[s])
            nav0 = new["sleeves"][s]["nav"]
            r = day_port_ret(
                prev_w, row_ret, cash_ret_mode="live",
                missing_out=missing, date=ds, sleeve=s,
            )
            nav1 = nav0 * (1.0 + r)
            turnover = 0.0
            rebalanced = False
            if ds in by_date:
                new_w = dict(by_date[ds].get(s) or cur_w[s])
                turnover = l1_turn(prev_w, new_w)
                if turnover > 1e-12:
                    nav1 *= (1.0 - COST * turnover)
                cur_w[s] = new_w
                rebalanced = True
            peak = max(new["sleeves"][s]["peak"], nav1)
            dd = nav1 / peak - 1.0
            mdd = min(new["sleeves"][s]["max_dd"], dd)
            new["sleeves"][s]["nav"] = nav1
            new["sleeves"][s]["peak"] = peak
            new["sleeves"][s]["max_dd"] = mdd
            new["sleeves"][s]["weights"] = dict(cur_w[s])
            new["sleeves"][s]["history"].append({
                "date": ds,
                "nav": round(nav1, 4),
                "day_ret": round(r, 6),
                "turnover": round(turnover, 6),
                "rebalanced": rebalanced,
            })
            if s in ("tech_C", "spy_C"):
                sn0 = new["shadow_cash0"][s]["nav"]
                r0 = day_port_ret(prev_w, row_ret, cash_ret_mode="zero")
                sn1 = sn0 * (1.0 + r0)
                if turnover > 1e-12:
                    sn1 *= (1.0 - COST * turnover)
                speak = max(new["shadow_cash0"][s]["peak"], sn1)
                sdd = sn1 / speak - 1.0
                new["shadow_cash0"][s]["nav"] = sn1
                new["shadow_cash0"][s]["peak"] = speak
                new["shadow_cash0"][s]["max_dd"] = min(new["shadow_cash0"][s]["max_dd"], sdd)
                new["shadow_cash0"][s]["history"].append({
                    "date": ds,
                    "nav": round(sn1, 4),
                    "day_ret": round(r0, 6),
                    "turnover": round(turnover, 6),
                })

    new["last_date"] = days[-1] if days else start
    new["updated"] = datetime.now(TZ).strftime("%Y-%m-%d %H:%M")
    if missing:
        seen = set()
        uniq = []
        for m in missing:
            k = (m.get("date"), m.get("sleeve"), m.get("ticker"))
            if k in seen:
                continue
            seen.add(k)
            uniq.append(m)
        new["missing_price_warnings"] = uniq[-50:]
        logger.warning(
            "missing_price_warnings=%d; cash-leg gaps would inflate timing cost like 0%% cash",
            len(uniq),
        )
    return new



def metrics_table(ledger: dict) -> str:
    lines = []
    lines.append(f"{HDR}\n")
    lines.append(
        f"> 本金各 {PRINCIPAL:,.0f} ｜ 单边成本 {COST:.2%} ｜ "
        f"起点 {ledger.get('start_date')} → 最新 {ledger.get('last_date')} ｜ "
        f"更新 {ledger.get('updated')}\n"
    )
    lines.append(
        "> 规则：按 `daily_abc_*.json` 目标权重滚动；有新快照则当日收盘再平衡。"
        "目标权重含分数闸+Trinity滤网。默认对照 C+Trinity生产 / A+Trinity研究；B折中。另附全仓 QQQ/SPY；C 另附现金=0影子。\n"
    )

    # summary table
    lines.append("### 累计绩效\n")
    lines.append("| 组合 | 净值 | 累计收益 | 最大回撤 | 近1日 | vs科技A | vs标普A |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|")

    def last_ret(s):
        h = ledger["sleeves"][s]["history"]
        if len(h) >= 2:
            return h[-1].get("day_ret") or 0.0
        return 0.0

    navs = {s: float(ledger["sleeves"][s]["nav"]) for s in SLEEVES}
    base_tech = navs.get("tech_A") or PRINCIPAL
    base_spy = navs.get("spy_A") or PRINCIPAL

    order = SLEEVES
    for s in order:
        nav = navs[s]
        tot = nav / PRINCIPAL - 1.0
        mdd = float(ledger["sleeves"][s].get("max_dd") or 0.0)
        d1 = last_ret(s)
        if s.startswith("tech") or s == "bh_QQQ":
            vs = (nav / base_tech - 1.0) * 100
            vs_s = f"{vs:+.1f}pp"
            vs2 = "—"
        elif s.startswith("spy") or s == "bh_SPY":
            vs2 = f"{(nav / base_spy - 1.0) * 100:+.1f}pp"
            vs_s = "—"
        else:
            vs_s = vs2 = "—"
        lines.append(
            f"| {LABELS[s]} | {nav:,.0f} | {tot*100:+.2f}% | {mdd*100:.2f}% | "
            f"{d1*100:+.2f}% | {vs_s} | {vs2} |"
        )

    # 现金=0 影子对照（生产 C）
    sh = ledger.get("shadow_cash0") or {}
    if sh.get("tech_C") and sh.get("spy_C"):
        lines.append("\n### 现金=0 影子对照（同 C 权重，BIL 段收益强制为 0）\n")
        lines.append("| 组合 | 含BIL净值 | 现金=0影子 | BIL贡献(pp) | 影子最大回撤 |")
        lines.append("|---|---:|---:|---:|---:|")
        for s, label in (("tech_C", "科技·C"), ("spy_C", "标普·C")):
            live = float(navs[s])
            shadow = float(sh[s]["nav"])
            contrib_pp = (live / PRINCIPAL - shadow / PRINCIPAL) * 100
            smdd = float(sh[s].get("max_dd") or 0.0)
            lines.append(
                f"| {label} | {live:,.0f} | {shadow:,.0f} | {contrib_pp:+.2f}pp | {smdd*100:.2f}% |"
            )
        lines.append(
            "\n> 若 BIL贡献 接近 0，说明样本内短债很薄；若显著为正，说明把空仓记成 0% 会夸大择时成本。\n"
        )
    warns = ledger.get("missing_price_warnings") or []
    if warns:
        lines.append(
            f"\n> ⚠️ 缺价告警 {len(warns)} 条（含可能的 BIL 缺口）；见 ledger JSON `missing_price_warnings`。\n"
        )

    # ranking within tech / spy
    lines.append("\n### 分组排名（累计收益）\n")
    for title, keys in (
        ("科技侧", ["tech_A", "tech_B", "tech_C", "bh_QQQ"]),
        ("标普侧", ["spy_A", "spy_B", "spy_C", "bh_SPY"]),
    ):
        ranked = sorted(keys, key=lambda k: navs[k], reverse=True)
        parts = [f"{i+1}.{LABELS[k]}({(navs[k]/PRINCIPAL-1)*100:+.1f}%)" for i, k in enumerate(ranked)]
        lines.append(f"- **{title}**：{' ｜ '.join(parts)}")

    # recent 10 days compact
    lines.append("\n### 近 10 个交易日净值\n")
    # collect dates from tech_A history
    hist = ledger["sleeves"]["tech_A"]["history"]
    tail = hist[-10:]
    if tail:
        dates = [h["date"] for h in tail]
        header = "| 日期 | " + " | ".join(LABELS[s] for s in ["tech_A", "tech_B", "tech_C", "spy_A", "spy_B", "spy_C"]) + " |"
        sep = "|---|" + "---:|" * 6
        lines.append(header)
        lines.append(sep)
        # index history by date per sleeve
        by = {}
        for s in SLEEVES:
            by[s] = {h["date"]: h["nav"] for h in ledger["sleeves"][s]["history"]}
        for d in dates:
            cells = [d]
            for s in ["tech_A", "tech_B", "tech_C", "spy_A", "spy_B", "spy_C"]:
                v = by[s].get(d)
                cells.append(f"{v:,.0f}" if v is not None else "—")
            lines.append("| " + " | ".join(cells) + " |")

    lines.append("\n### 最新目标权重快照\n")
    for s in ["tech_A", "tech_B", "tech_C", "spy_A", "spy_B", "spy_C"]:
        w = ledger["sleeves"][s].get("weights") or {}
        if not w:
            continue
        ws = " · ".join(f"{k} {v*100:.0f}%" for k, v in sorted(w.items(), key=lambda x: -x[1]))
        lines.append(f"- **{LABELS[s]}**：{ws}")

    lines.append(
        "\n> 纸面模拟，非实盘；费用仅计再平衡换手单边成本。"
        "后续优化可直接对比 tech_A/B/C 与 spy_A/B/C 累计收益与回撤。\n"
    )
    return "\n".join(lines)


def upsert_md(path, block, header=HDR):
    if not path or not os.path.isfile(path):
        return "skip"
    txt = open(path, encoding="utf-8").read()
    if header in txt:
        idx = txt.index(header)
        rest = txt[idx + len(header):]
        m = re.search(r"\n## ", rest)
        if m:
            end = idx + len(header) + m.start()
            txt = txt[:idx].rstrip() + "\n\n" + block + "\n" + txt[end:].lstrip("\n")
        else:
            txt = txt[:idx].rstrip() + "\n\n" + block + "\n"
        open(path, "w", encoding="utf-8").write(txt)
        return "updated"
    if not txt.endswith("\n"):
        txt += "\n"
    open(path, "w", encoding="utf-8").write(txt + "\n" + block + "\n")
    return "appended"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--abc", default=None, help="指定单日 daily_abc json；默认加载全部")
    ap.add_argument("--ledger", default=LEDGER_PATH)
    ap.add_argument("--append-to", action="append", default=[], help="把对比表 upsert 进这些 md，可多次")
    ap.add_argument("--md-out", default=None, help="独立 md 输出路径")
    args = ap.parse_args()

    if args.abc:
        d = _load_json(args.abc)
        ds = d.get("date") or re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(args.abc)).group(1)
        snaps = [(ds, d)]
    else:
        files = discover_abc_files()
        if not files:
            print("[FAIL] 未找到 daily_abc_*.json", flush=True)
            raise SystemExit(1)
        snaps = [(ds, _load_json(fp)) for ds, fp in files]

    # ensure dates sorted
    snaps.sort(key=lambda x: x[0])
    start, end = snaps[0][0], date.today().isoformat()

    # collect tickers
    by_date = {ds: snapshot_targets(abc) for ds, abc in snaps}
    tickers = universe_tickers(by_date)
    print(f"[INFO] snapshots={len(snaps)} {start}->{end} tickers={tickers}", flush=True)
    px = download_prices(tickers, start, end)
    print(f"[INFO] prices {px.index[0].date()} -> {px.index[-1].date()} rows={len(px)}", flush=True)

    ledger = rebuild_from_snapshots(empty_ledger(), snaps, px)
    _save_json(args.ledger, ledger)
    print(f"[OK] ledger -> {args.ledger}", flush=True)

    table = metrics_table(ledger)
    md_out = args.md_out or os.path.join(RES, f"paper_abc_nav_{date.today().isoformat()}.md")
    open(md_out, "w", encoding="utf-8").write(
        f"# 纸面净值累计对比 — {date.today().isoformat()}\n\n" + table
    )
    print(f"[OK] md -> {md_out}", flush=True)

    # default append targets
    appends = list(args.append_to)
    # auto daily instruction today
    auto_instr = os.path.join(RES, f"daily_instruction_{date.today().isoformat()}.md")
    if os.path.isfile(auto_instr) and auto_instr not in appends:
        appends.append(auto_instr)

    for path in appends:
        st = upsert_md(path, table)
        print(f"[OK] upsert {st} -> {path}", flush=True)

    # print summary ranks
    navs = {s: ledger["sleeves"][s]["nav"] for s in ["tech_A", "tech_B", "tech_C"]}
    ranked = sorted(navs.items(), key=lambda x: -x[1])
    print("[TECH rank] " + " > ".join(f"{LABELS[k]}={v:,.0f}" for k, v in ranked), flush=True)
    navs = {s: ledger["sleeves"][s]["nav"] for s in ["spy_A", "spy_B", "spy_C"]}
    ranked = sorted(navs.items(), key=lambda x: -x[1])
    print("[SPY rank]  " + " > ".join(f"{LABELS[k]}={v:,.0f}" for k, v in ranked), flush=True)


if __name__ == "__main__":
    main()
