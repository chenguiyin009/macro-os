"""pytest 全局配置"""
import os
import sys
from pathlib import Path

# 将项目根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Keep the SOXX tech-drawdown fetch (yfinance network) OFF during the test
# session. Production leaves MACRO_OS_TECH_DRAWDOWN_ENABLED at its default "1".
os.environ.setdefault("MACRO_OS_TECH_DRAWDOWN_ENABLED", "0")
