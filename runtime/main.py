"""Macro OS - Entry point.

Usage:
    python -m runtime.main                                       # Run one pipeline cycle
    python -m runtime.main --loop                                # Run scheduler loop
    python -m runtime.main --dry-run                             # Dry run (no events, no notifications)
    python -m runtime.main --dry-run --output /tmp/decision.json # Dry run with file output (CI/CD friendly)
"""

from __future__ import annotations

import argparse
import logging
import sys
import signal
import os
from pathlib import Path

# ================================================================
# 绝对鲁棒的路径注入 (Robust Path Bootstrap)
# 确保无论是 `python runtime/main.py` 还是 `python -m runtime.main`，
# 仓库根目录始终位于 sys.path 首位，彻底杜绝 ModuleNotFoundError。
# ================================================================
_project_root = Path(__file__).resolve().parents[1]
if str(_project_root) not in sys.path:
    sys.path.insert(0, str(_project_root))

# ----------------------------------------------------------------
# Optional override for containerized / Docker deployments.
# 兼容原 MACRO_OS_ROOT 环境变量注入，不影响默认行为。
# ----------------------------------------------------------------
if os.environ.get("MACRO_OS_ROOT"):
    _env_root = os.environ["MACRO_OS_ROOT"]
    if _env_root not in sys.path:
        sys.path.insert(0, _env_root)

# 此后方可安全导入内部业务模块
# ----------------------------------------------------------------

# ================ Imports ============================
from config.settings import settings
from core.config_validation import validate_macro_configuration
from adapters.tradingview import TradingViewAdapter
from adapters.feishu import FeishuAdapter
from adapters.vault import VaultAdapter
from runtime.orchestrator import Orchestrator
from runtime.scheduler import Scheduler

# ================ Logging Setup ======================
def setup_logging() -> logging.Logger:
    """灏佽鏃ュ織閰嶇疆锛岄伩鍏嶅鍏ユ椂姹℃煋鍏ㄥ眬 Root Logger"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger("macro-os")

logger = setup_logging()

# ================ Factory ============================
def build_orchestrator() -> Orchestrator:
    """Build orchestrator from settings. Adapters should implement lazy connection."""
    tv = TradingViewAdapter(
        mcp_command=settings.mcp_command,
        mcp_script_path=settings.mcp_script_path,
        timeout_seconds=settings.mcp_timeout_seconds,
    )
    vault = VaultAdapter(settings.events_path)
    feishu = FeishuAdapter(webhook_url=settings.feishu_webhook_url)

    config = settings.thresholds.model_dump() if settings.thresholds else {}
    validate_macro_configuration(config, settings.watchlist)

    return Orchestrator(
        tradingview=tv,
        vault=vault,
        feishu=feishu,
        config=config,
    )

# ================ Main Entry =========================
def main() -> None:
    parser = argparse.ArgumentParser(description="Macro OS - Macro Decision Engine")
    parser.add_argument("--loop", action="store_true", help="Run scheduler loop instead of single cycle")
    parser.add_argument("--dry-run", action="store_true", help="Run pipeline without writing events or sending notifications")
    parser.add_argument("--output", type=str, default=None, help="Write decision to file (useful for CI/CD automation)")
    parser.add_argument("--interval", type=int, default=None, help="Scheduler interval in minutes (overrides config)")
    args = parser.parse_args()

    logger.info("Macro OS starting (loop=%s, dry_run=%s)", args.loop, args.dry_run)

    # 1. Boot & Validate (Fail-fast phase)
    try:
        orchestrator = build_orchestrator()
    except ValueError as exc:
        logger.critical("Configuration invalid. Please verify YAML thresholds and watchlist: %s", exc, exc_info=True)
        sys.exit(1)
    except Exception as exc:
        logger.critical("Failed to build orchestrator due to unexpected error: %s", exc, exc_info=True)
        sys.exit(1)

    # 2. Dry-Run Mode
    if args.dry_run:
        logger.info("Executing dry-run pipeline...")
        decision, _ = orchestrator.dry_run()
        if decision is None:
            logger.error("Dry-run failed to generate a decision.")
            sys.exit(1)
        output_json = decision.model_dump_json(indent=2)
        
        if args.output:
            output_path = Path(args.output)
            output_path.write_text(output_json, encoding="utf-8")
            logger.info("Dry-run decision written successfully to %s", args.output)
        else:
            print(output_json)
        return

    # 3. Loop Mode (Daemon)
    if args.loop:
        interval = args.interval or settings.scheduler_interval_minutes
        scheduler = Scheduler(orchestrator=orchestrator, interval_minutes=interval)
        
        # 浼橀泤鍏抽棴淇″彿澶勭悊 (Graceful Shutdown for Docker/Kubernetes)
        def handle_shutdown(signum, frame):
            logger.info("Received termination signal (%s), shutting down gracefully...", signum)
            scheduler.stop()
            
        signal.signal(signal.SIGTERM, handle_shutdown)
        signal.signal(signal.SIGINT, handle_shutdown)

        try:
            scheduler.run_loop()
        except Exception as exc:
            logger.error("Scheduler loop crashed: %s", exc, exc_info=True)
            sys.exit(1)
        return

    # 4. Single Run Mode (CronJob Triggered)
    try:
        decision = orchestrator.run_pipeline()
        
        # Error Classification & Alerting
        if decision is None:
            logger.error("Pipeline failed to generate a decision. Check upstream adapter logs.")
            if hasattr(orchestrator, 'feishu'):
                orchestrator.feishu.send_alert("馃敶 Macro OS Alert: Pipeline execution returned None. Data pipeline may be degraded.")
            sys.exit(1)
        
        print(decision.model_dump_json(indent=2))
        
    except Exception as exc:
        logger.critical("Pipeline execution crashed ungracefully: %s", exc, exc_info=True)
        if hasattr(orchestrator, 'feishu'):
            orchestrator.feishu.send_alert(f"馃毃 Macro OS Critical Crash: {exc}")
        sys.exit(1)

if __name__ == "__main__":
    main()

