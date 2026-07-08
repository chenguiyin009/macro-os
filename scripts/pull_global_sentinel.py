"""Macro OS — Pull Global Sentinel from QQQ chart via Chrome CDP."""

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = r"C:\Users\chengy12005\Documents\tradingview"
NODE = r"D:\Program Files\nodejs\node.exe"
RELAY = r"C:\Users\chengy12005\Documents\tradingview\relay\tv-desktop-monitor.mjs"
WEBHOOK = "http://127.0.0.1:8020/webhook/global-sentinel"


def extract_global_sentinel(snapshot: dict) -> dict | None:
    for chart in snapshot.get("charts", []):
        for study in chart.get("studies", []):
            name = study.get("name", "")
            if "Global Sentinel" not in name:
                continue
            vals = study.get("values", [])
            payload = {"schema": "macro_os.v5_pull", "script": "Global Sentinel"}
            for v in vals:
                title = v.get("title", "")
                value = str(v.get("value", "")).strip()
                if "M1 Score" in title and value:
                    payload["m1_score"] = int(float(value))
                elif "M2 Score" in title and value:
                    payload["m2_score"] = int(float(value))
                elif "M3 Score" in title and value:
                    payload["m3_score"] = int(float(value))
                elif title == "Danger" and value:
                    payload["danger_score"] = int(float(value))
                elif "Composite" in title and value and value != "0":
                    try:
                        payload["composite_score"] = round(float(value), 4)
                    except ValueError:
                        pass
            for v in vals:
                title = v.get("title", "")
                value = str(v.get("value", "")).strip()
                if "Composite Regime" in title:
                    payload["composite_regime"] = value
                elif title == "Danger Regime":
                    payload["danger_regime"] = value
                elif "Gold Regime" in title:
                    payload["gold_regime"] = value
                elif "DXY / VIX" in title and "/" in value:
                    parts = value.split("/")
                    try:
                        payload["dxy"] = float(parts[0].strip())
                        payload["vix"] = float(parts[1].strip())
                    except ValueError:
                        pass
                elif "QQQ / GLD" in title and "/" in value:
                    parts = value.split("/")
                    try:
                        payload["qqq"] = float(parts[0].strip())
                        payload["gld"] = float(parts[1].strip())
                    except ValueError:
                        pass
                elif "HYG / IEF" in title and "/" in value:
                    parts = value.split("/")
                    try:
                        payload["hyg"] = float(parts[0].strip())
                        payload["ief"] = float(parts[1].strip())
                    except ValueError:
                        pass
            payload.setdefault("composite_regime", "NEUTRAL")
            payload.setdefault("composite_score", 0.0)
            payload.setdefault("m1_score", 0)
            payload.setdefault("m2_score", 0)
            payload.setdefault("m3_score", 0)
            payload.setdefault("danger_score", 0)
            payload.setdefault("danger_regime", "NEUTRAL")
            payload.setdefault("gold_regime", "Neutral")
            payload.setdefault("vix", 20.0)
            payload.setdefault("dxy", 100.0)
            payload.setdefault("gld", 2300.0)
            payload.setdefault("qqq", 0.0)
            payload.setdefault("hyg", 0.0)
            payload.setdefault("ief", 0.0)
            payload.setdefault("time", "")
            return payload
    return None


def main():
    print("Running CDP relay snapshot...")
    result = subprocess.run(
        [NODE, RELAY, "--once"], capture_output=True, timeout=30, cwd=PROJECT_ROOT,
    )
    raw = result.stdout.decode("utf-8", errors="replace")
    if not raw.strip():
        stderr = result.stderr.decode("utf-8", errors="replace")
        print("ERROR: relay returned no stdout", file=sys.stderr)
        if stderr:
            print(f"stderr: {stderr[:500]}", file=sys.stderr)
        return 1
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        print(f"ERROR: invalid JSON: {e}", file=sys.stderr)
        return 1
    if not data.get("ok"):
        print(f"ERROR: relay failed: {raw[:200]}", file=sys.stderr)
        return 1
    payload = extract_global_sentinel(data)
    if not payload:
        print("Global Sentinel study not found. Is it added to the QQQ chart?", file=sys.stderr)
        return 2
    print(f"Extracted: composite={payload['composite_regime']} M1={payload['m1_score']} M2={payload['m2_score']} M3={payload['m3_score']} Danger={payload['danger_score']}")
    pdata = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(WEBHOOK, data=pdata, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            body = resp.read().decode("utf-8")
            print(f"Webhook response: {resp.status} {body}")
            return 0
    except (urllib.error.URLError, urllib.error.HTTPError, OSError) as e:
        print(f"ERROR: webhook failed: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())