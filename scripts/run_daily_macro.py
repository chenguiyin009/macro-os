#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Daily macro three-piece runner.

Runs (in order):
  1) theme_state_machine_daily.py   (latest available bars)
  2) daily_tech_dampener.py
  3) denominator_state_daily.py     (best-effort; may lag on FRED)
  4) daily_macro_consolidated.py

Writes artifacts under tradingview/output/ and a dated snapshot folder:
  output/snapshot_YYYY-MM-DD/
  output/snapshot_YYYY-MM-DD/FULL_DAILY_READOUT.md
  output/logs/daily_run_YYYY-MM-DD.log

Usage:
  python macro-os/scripts/run_daily_macro.py
  python macro-os/scripts/run_daily_macro.py --date 2026-07-20
  python macro-os/scripts/run_daily_macro.py --force-refresh
  python macro-os/scripts/run_daily_macro.py --skip-denom
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

ROOT = Path(__file__).resolve().parents[2]  # tradingview/
MACRO = Path(__file__).resolve().parents[1]  # macro-os/
SCRIPTS = MACRO / "scripts"
DEFAULT_OUT = ROOT / "output"
DEFAULT_PROXY = "http://127.0.0.1:7890"


def _ensure_proxy() -> None:
    if os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY"):
        return
    os.environ.setdefault("HTTPS_PROXY", DEFAULT_PROXY)
    os.environ.setdefault("HTTP_PROXY", DEFAULT_PROXY)


def _run(cmd: List[str], log: logging.Logger, timeout: int = 600) -> int:
    log.info("RUN %s", " ".join(cmd))
    try:
        p = subprocess.run(
            cmd,
            cwd=str(MACRO),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        log.error("TIMEOUT: %s", cmd)
        return 124
    except Exception as exc:  # noqa: BLE001
        log.error("FAIL %s: %s", cmd, exc)
        return 1
    if p.stdout:
        for line in p.stdout.splitlines()[-40:]:
            log.info("[out] %s", line)
    if p.returncode != 0 and p.stderr:
        for line in p.stderr.splitlines()[-30:]:
            log.warning("[err] %s", line)
    log.info("EXIT %s -> %s", Path(cmd[1]).name if len(cmd) > 1 else cmd, p.returncode)
    return int(p.returncode)


def _newest(dir_path: Path, pattern: str) -> Optional[Path]:
    hits = list(dir_path.glob(pattern))
    if not hits:
        return None
    return sorted(hits, key=lambda p: p.stat().st_mtime)[-1]


def _write_full_readout(out: Path, snap: Path, date_str: str, log: logging.Logger) -> Path:
    parts: List[str] = []
    header = [
        f"# FULL DAILY READOUT | {date_str}",
        "",
        f"- generated_at: {dt.datetime.now().isoformat(timespec='seconds')}",
        f"- output_dir: {out}",
        "",
    ]
    parts.append("\n".join(header))

    # consolidated first
    for name in [
        f"daily_macro_{date_str}.md",
        # theme may be dated by latest bar, not calendar date
    ]:
        p = out / name
        if p.exists():
            parts.append(f"## {name}\n\n" + p.read_text(encoding="utf-8"))

    theme = _newest(out, "theme_state_machine_*.md")
    if theme:
        parts.append(f"## {theme.name}\n\n" + theme.read_text(encoding="utf-8"))
        # also copy matching json
        tj = theme.with_suffix(".json")
        if tj.exists():
            shutil.copy2(tj, snap / tj.name)
        shutil.copy2(theme, snap / theme.name)

    tech_md = out / f"tech_dampener_decision_{date_str}.md"
    tech_js = out / f"tech_drawdown_{date_str}.json"
    if tech_md.exists():
        parts.append(f"## {tech_md.name}\n\n" + tech_md.read_text(encoding="utf-8"))
        shutil.copy2(tech_md, snap / tech_md.name)
    if tech_js.exists():
        shutil.copy2(tech_js, snap / tech_js.name)

    # Canonical denominator = the file the consolidated actually referenced.
    # The headless port writes by FRED date (lags the report date), so prefer
    # that exact file over "newest by name" (which could be a stale TV run).
    # Fall back to newest by name if the referenced file is absent.
    dmc_json = out / f"daily_macro_{date_str}.json"
    denom_date = None
    if dmc_json.exists():
        try:
            _dmc = json.loads(dmc_json.read_text(encoding="utf-8"))
            denom_date = (_dmc.get("as_of") or {}).get("denominator")
        except Exception:  # noqa: BLE001
            log.warning("parse dmc json for denom date failed")
    dens = sorted(
        [p for p in out.glob("denominator_state_*.md") if "analysis" not in p.name],
        key=lambda p: p.name,
    )
    d = None
    if denom_date:
        cand = out / f"denominator_state_{denom_date}.md"
        if cand.exists():
            d = cand
    if d is None and dens:
        d = dens[-1]
    if d:
        parts.append(f"## {d.name}\n\n" + d.read_text(encoding="utf-8"))
        shutil.copy2(d, snap / d.name)
        dj = d.with_suffix(".json")
        if dj.exists():
            shutil.copy2(dj, snap / dj.name)

    for name in [f"daily_macro_{date_str}.md", f"daily_macro_{date_str}.json"]:
        p = out / name
        if p.exists():
            shutil.copy2(p, snap / name)

    full = snap / "FULL_DAILY_READOUT.md"
    full.write_text("\n\n---\n\n".join(parts) + "\n", encoding="utf-8")
    log.info("snapshot full readout: %s", full)
    return full


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description="Run daily macro three-piece pack")
    parser.add_argument("--date", default=dt.date.today().isoformat(), help="YYYY-MM-DD")
    parser.add_argument("--report-dir", default=str(DEFAULT_OUT))
    parser.add_argument("--force-refresh", action="store_true", help="refresh yfinance caches")
    parser.add_argument("--skip-denom", action="store_true")
    parser.add_argument("--skip-tech", action="store_true")
    parser.add_argument("--skip-theme", action="store_true")
    args = parser.parse_args(argv)

    _ensure_proxy()
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    out = Path(args.report_dir)
    out.mkdir(parents=True, exist_ok=True)
    log_dir = out / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    snap = out / f"snapshot_{args.date}"
    snap.mkdir(parents=True, exist_ok=True)

    log_path = log_dir / f"daily_run_{args.date}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[
            logging.FileHandler(log_path, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )
    log = logging.getLogger("run-daily-macro")
    log.info("=== daily macro start date=%s out=%s ===", args.date, out)

    py = sys.executable
    rc_theme = rc_tech = rc_denom = 0

    if not args.skip_theme:
        theme_cmd = [py, str(SCRIPTS / "theme_state_machine_daily.py"), "--report-dir", str(out)]
        if args.force_refresh:
            theme_cmd.append("--force-refresh")
        # expected as-of = report calendar date for lag diagnostics
        theme_cmd += ["--expected-as-of", args.date]
        rc_theme = _run(theme_cmd, log, timeout=900)

    if not args.skip_tech:
        rc_tech = _run(
            [py, str(SCRIPTS / "daily_tech_dampener.py"), "--date", args.date, "--report-dir", str(out)],
            log,
            timeout=600,
        )

    if not args.skip_denom:
        denom_script = SCRIPTS / "denominator_state_daily.py"
        if denom_script.exists():
            rc_denom = _run(
                [py, str(denom_script), "--report-dir", str(out)],
                log,
                timeout=600,
            )
        else:
            log.warning("denominator script missing; skip")

    # consolidated: reuse just-produced artifacts
    rc_dmc = _run(
        [
            py,
            str(SCRIPTS / "daily_macro_consolidated.py"),
            "--date",
            args.date,
            "--report-dir",
            str(out),
            "--no-run-theme",
            "--no-run-tech",
            "--no-run-denom",
        ],
        log,
        timeout=300,
    )

    full = _write_full_readout(out, snap, args.date, log)

    # machine summary json
    summary = {
        "date": args.date,
        "exit_codes": {
            "theme": rc_theme,
            "tech": rc_tech,
            "denominator": rc_denom,
            "consolidated": rc_dmc,
        },
        "snapshot_dir": str(snap),
        "full_readout": str(full),
        "log": str(log_path),
    }
    # attach key fields if present
    dmc_json = out / f"daily_macro_{args.date}.json"
    if dmc_json.exists():
        try:
            summary["daily_macro"] = json.loads(dmc_json.read_text(encoding="utf-8")).get("synthesis")
            summary["as_of"] = json.loads(dmc_json.read_text(encoding="utf-8")).get("as_of")
        except Exception as exc:  # noqa: BLE001
            log.warning("parse dmc json failed: %s", exc)
    theme_json = _newest(out, "theme_state_machine_*.json")
    if theme_json:
        try:
            tj = json.loads(theme_json.read_text(encoding="utf-8"))
            summary["theme"] = {
                "date": tj.get("date"),
                "dominant_theme": tj.get("dominant_theme"),
                "today_leaders": tj.get("today_leaders"),
                "quality": tj.get("quality"),
                "risk_bias": tj.get("risk_bias"),
            }
        except Exception as exc:  # noqa: BLE001
            log.warning("parse theme json failed: %s", exc)

    summary_path = snap / "run_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # also root pointer for "latest"
    (out / "LATEST_DAILY_SNAPSHOT.txt").write_text(str(snap) + "\n", encoding="utf-8")
    log.info("summary: %s", summary_path)
    log.info("=== daily macro done ===")

    # non-zero if core theme/dmc failed
    if rc_theme != 0 or rc_dmc != 0:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())