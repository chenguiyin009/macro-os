from core.agents.cio_agent import CIOAgent
from core.schemas import FeatureSchema


def test_cio_renders_funding_price_research_block() -> None:
    report = CIOAgent().generate(
        regime_probs={},
        allocation={"QQQ": 0.0, "CASH": 1.0},
        diff_report=None,
        shadow_report="",
        features_summary=FeatureSchema(vix=18.0, dxy=101.0, tips_yield=2.3),
        funding_price_research={
            "quadrant": "Q1_STRESS_TEST",
            "label_zh": "压力测试",
            "real_rate_direction": "up",
            "nominal_rate_direction": "up",
            "hard_regime_hint": "TIGHT_LIQUIDITY",
            "transmission_layer": "duration_valuation",
            "notes": "久期重新定价，不是美元荒或信用危机",
        },
    )
    assert "资金价格四象限" in report
    assert "Q1_STRESS_TEST" in report
    assert "TIGHT_LIQUIDITY" in report
    assert "久期重新定价" in report
