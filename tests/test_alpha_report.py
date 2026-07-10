"""generate_alpha_report.py 审计脚本测试"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from scripts.generate_alpha_report import (
    generate_report,
    load_ledger,
    load_market_data,
    main,
    print_report,
    reconstruct_factors,
)
from trinity.aggregator import DiagnosticReport
from trinity.context import OHLCV
from trinity.ledger import EventSourcingTracker
from trinity.orchestrator import Orchestrator


@pytest.fixture
def populated_ledger():
    """创建一个有多个事件的 Ledger 文件 (>=10 条, 满足统计显著性铁律门槛)"""
    orch = Orchestrator()
    for i in range(12):
        orch.run(symbol=f"SYMBOL_{i}", bars=100, dry_run=True, seed=42 + i)

    filepath = tempfile.mktemp(suffix=".json")
    orch.save_ledger(filepath)
    yield filepath
    if os.path.exists(filepath):
        os.unlink(filepath)


class TestLoadLedger:
    def test_load_existing(self, populated_ledger):
        events = load_ledger(populated_ledger)
        assert len(events) > 0
        assert isinstance(events[0], dict)

    def test_load_missing(self):
        with pytest.raises(FileNotFoundError):
            load_ledger("/nonexistent/path.json")


class TestReconstructFactors:
    def test_basic_reconstruction(self):
        """从事件中重构因子"""
        event = {
            "contexts": {
                "J+2": {"state": "极强", "price": 100, "ma55": 95},
                "J": {"structure": "A"},
                "J-1": {"boll_mid": 99, "dif": 0.5, "dea": 0.3, "macd_hist": 0.4},
            },
            "decision": {"spacetime_overall": 0.85, "action": "STRONG_ADD"},
        }
        factors = reconstruct_factors(event)
        assert len(factors) >= 4
        # 状态因子
        state_f = [f for f in factors if f.factor == "STATE"]
        assert len(state_f) == 1
        assert state_f[0].value == 0.9  # 极强 → 0.9

    def test_antiamnesia_format(self):
        """支持 AntiAmnesiaTracker 格式"""
        event = {
            "context_snapshot": {
                "state_j2": "极强",
                "structure": "A",
                "price": 100,
                "j_ma55": 95,
            },
            "decision": {"action": "STRONG_ADD"},
        }
        factors = reconstruct_factors(event)
        assert len(factors) >= 3


class TestGenerateReport:
    def test_report_with_ledger(self, populated_ledger):
        """从 Ledger 生成报告"""
        report = generate_report(
            ledger_filepath=populated_ledger,
            symbol="TEST",
            bars=200,
            seed=42,
            fixed_horizon=10,
        )
        assert isinstance(report, DiagnosticReport)
        assert report.sample_size > 0

    def test_empty_ledger(self):
        """空 Ledger 不崩溃"""
        filepath = tempfile.mktemp(suffix=".json")
        with open(filepath, "w") as f:
            json.dump({"version": "2.1.0", "events": []}, f)
        try:
            report = generate_report(filepath)
            assert report.sample_size == 0
            assert any("空" in r or "不足" in r for r in report.recommendations)
        finally:
            os.unlink(filepath)

    def test_report_has_recommendations(self, populated_ledger):
        report = generate_report(populated_ledger, bars=200, seed=42, fixed_horizon=10)
        assert len(report.recommendations) > 0

    def test_report_with_market_data(self, populated_ledger):
        """使用指定市场数据"""
        market_data = [
            OHLCV(timestamp=i, open=10+i*0.3, high=11+i*0.3, low=9+i*0.3, close=10+i*0.3, volume=1000)
            for i in range(200)
        ]
        report = generate_report(
            populated_ledger, market_data=market_data, fixed_horizon=10,
        )
        assert report.sample_size > 0


class TestAttributionLoop:
    """闭环回归: Ledger 事件 → OutcomeSimulator 收益 → PerformanceAggregator 因子归因

    现有测试只断言 sample_size>0, 没有断言归因本身是否产生。
    若未来有人破坏 reconstruct_factors → aggregator.add_decision 的接线
    (例如因子重建返回空列表, 或 add_decision 不保存因子),
    这里的断言会失败, 从而保护"三位一体"归因闭环的核心价值。
    """

    def test_multi_event_produces_factor_alpha(self, populated_ledger):
        """多事件账本必须产出因子 Alpha 排名 (而非空)"""
        report = generate_report(
            populated_ledger, bars=240, seed=42, fixed_horizon=10,
        )
        assert report.sample_size >= 2
        # 关键断言: 闭环确实产生了因子归因
        assert len(report.top_factors) > 0, (
            "归因闭环损坏: 多事件账本未产出任何因子 Alpha, "
            "请检查 reconstruct_factors / aggregator.add_decision 接线"
        )
        # 每个因子 Alpha 都是 (因子名, 数值) 元组
        for name, alpha in report.top_factors:
            assert isinstance(name, str) and name
            assert isinstance(alpha, float)

    def test_single_event_cannot_attribute(self, tmp_path):
        """单事件账本: 因子落在单一桶内, 不应误报归因 (设计限制, 需显式覆盖)"""
        filepath = tmp_path / "single.json"
        filepath.write_text(json.dumps({
            "version": "2.1.0",
            "events": [{
                "contexts": {
                    "J+2": {"state": "强", "price": 100, "ma55": 95},
                    "J": {"structure": "A"},
                    "J-1": {"boll_mid": 99, "dif": 0.5, "dea": 0.3, "macd_hist": 0.4},
                },
                "decision": {"spacetime_overall": 0.85, "action": "STRONG_ADD"},
            }],
        }, ensure_ascii=False))
        report = generate_report(
            str(filepath), bars=200, seed=42, fixed_horizon=10,
        )
        # 单事件下因子无法跨高/低桶拆分, top_factors 应为空
        # (这是因子 Alpha 的设计前提, 不是 bug — 用此测试固化该语义)
        assert report.sample_size == 1
        assert report.top_factors == []
        # 但整体统计与建议仍应正常产出, 不崩溃
        assert len(report.recommendations) >= 1

    def test_recommendations_are_actionable(self, populated_ledger):
        """归因建议应为可执行的权重调整项 (含因子名)"""
        report = generate_report(
            populated_ledger, bars=240, seed=42, fixed_horizon=10,
        )
        assert any("权重" in r for r in report.recommendations)

    def test_significance_guard_blocks_weight_advice_under_10(self, tmp_path):
        """铁律#3: 样本 < 10 时禁止触发权重建议, 返回样本不足警示

        防止在小样本上过度优化 (over-fitting)。
        """
        filepath = tmp_path / "small.json"
        events = []
        for i, state in enumerate(["极强", "强", "中偏强", "中偏弱", "弱"]):
            events.append({
                "contexts": {
                    "J+2": {"state": state, "price": 100, "ma55": 95},
                    "J": {"structure": "A" if state in ("极强", "强") else "C"},
                    "J-1": {"boll_mid": 99, "dif": 0.5, "dea": 0.3, "macd_hist": 0.4},
                },
                "decision": {
                    "spacetime_overall": 0.85 if i % 2 == 0 else 0.2,
                    "action": "STRONG_ADD",
                },
            })
        filepath.write_text(json.dumps(
            {"version": "2.1.0", "events": events}, ensure_ascii=False))
        report = generate_report(str(filepath), bars=200, seed=42, fixed_horizon=10)
        assert report.sample_size == 5
        # 必须返回样本不足警示
        assert any("样本不足" in r for r in report.recommendations)
        # 不得出现可执行的权重调仓建议 (形如 "增加 X 权重" / "降低 X 权重")
        # 注: 警示语本身含"权重"二字, 故需用动作模式区分
        weight_advice = re.compile(r"(增加|降低).*权重")
        assert not any(weight_advice.search(r) for r in report.recommendations)
        # 因子排名应被抑制
        assert report.top_factors == []


class TestRealLedger:
    """守护真实账本 data/ledger.json 的丰富度与归因闭环

    该账本是"注入不同因子组合的模拟数据"的落地 (覆盖全部决策 regime),
    本类防止有人误将其缩减回单事件, 或破坏 Ledger→OutcomeSimulator→
    PerformanceAggregator 闭环。
    """

    LEDGER = os.path.join(os.path.dirname(__file__), "..", "data", "ledger.json")

    def test_rich_ledger_has_enough_samples(self):
        events = load_ledger(self.LEDGER)
        assert len(events) >= 15, f"账本样本不足 ({len(events)}), 归因无统计意义"

    def test_rich_ledger_covers_decision_regimes(self):
        events = load_ledger(self.LEDGER)
        actions = {e["decision"]["action"] for e in events}
        assert len(actions) >= 6, f"决策类型覆盖不足: {sorted(actions)}"

    def test_rich_ledger_produces_attribution(self):
        report = generate_report(self.LEDGER, bars=400, seed=42, fixed_horizon=20)
        assert report.sample_size >= 15
        # 闭环在真实账本上必须产出因子归因
        assert len(report.top_factors) > 0, "真实账本未产出因子 Alpha, 闭环损坏"


class TestPrintReport:
    def test_print_no_crash(self, populated_ledger):
        """打印报告不崩溃"""
        report = generate_report(populated_ledger, bars=200, seed=42, fixed_horizon=10)
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            print_report(report, symbol="TEST")
        output = buf.getvalue()
        assert "Alpha 归因分析报告" in output
        assert "样本数" in output
        assert "权重优化建议" in output


class TestCLI:
    def test_main_success(self, populated_ledger):
        """CLI 成功运行"""
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["--ledger", populated_ledger, "--bars", "200", "--seed", "42", "--fixed-horizon", "10"])
        assert ret == 0
        output = buf.getvalue()
        assert "Trinity OS" in output

    def test_main_json_output(self, populated_ledger):
        """JSON 输出"""
        import io
        from contextlib import redirect_stdout
        buf = io.StringIO()
        with redirect_stdout(buf):
            ret = main(["--ledger", populated_ledger, "--bars", "200", "--seed", "42",
                        "--fixed-horizon", "10", "--json"])
        assert ret == 0
        data = json.loads(buf.getvalue())
        assert "sample_size" in data
        assert "recommendations" in data

    def test_main_missing_ledger(self):
        ret = main(["--ledger", "/nonexistent/path.json"])
        assert ret == 1
