from __future__ import annotations

from runtime.portfolio_manager import PortfolioConstructionPipeline


def test_pipeline_loads_default_watchlist_config() -> None:
    pipeline = PortfolioConstructionPipeline()

    assert "QQQ" in pipeline.sizer.targets
    assert pipeline.sizer.targets["QQQ"].max_portfolio_weight == 0.40


def test_pipeline_applies_watchlist_max_weight_as_hard_limit() -> None:
    watchlist = {
        "QQQ": {
            "macro_sensitivity": ["RATES"],
            "moat_score": 0.6,
            "logic_stability": 0.5,
            "has_active_catalyst": True,
            "atr_percent_20d": 0.018,
            "beta_to_spy": 1.15,
            "max_portfolio_weight": 0.40,
        },
        "GLD": {
            "macro_sensitivity": ["RATES", "CREDIT"],
            "moat_score": 0.9,
            "logic_stability": 0.8,
            "has_active_catalyst": True,
            "atr_percent_20d": 0.011,
            "beta_to_spy": 0.10,
            "max_portfolio_weight": 0.60,
        },
    }
    pipeline = PortfolioConstructionPipeline(watchlist)

    assert pipeline.hard_limits["QQQ"] == 0.40
    assert pipeline.hard_limits["GLD"] == 0.60

    final = pipeline.run_pipeline(
        div_phase="NONE",
        fractures=[],
        features={},
        base_weights={"QQQ": 0.8, "GLD": 0.2},
        current_date="2026-09-01",
    )

    assert final["QQQ"] <= 0.40 + 1e-9
