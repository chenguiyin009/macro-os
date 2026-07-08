"""v4.3.1 ? Kernel regression: no probabilistic logic in kernel."""

from __future__ import annotations

from pathlib import Path

import core.decision_kernel as dk


class TestNoProbabilisticLogic:
    def test_no_weighted_string(self) -> None:
        src = Path(dk.__file__).read_text(encoding="utf-8")
        assert "weighted" not in src.lower()

    def test_no_vote_string(self) -> None:
        src = Path(dk.__file__).read_text(encoding="utf-8")
        assert "vote" not in src.lower()

    def test_no_softmax_string(self) -> None:
        src = Path(dk.__file__).read_text(encoding="utf-8")
        assert "softmax" not in src.lower()

    def test_no_probabilistic_blending(self) -> None:
        src = Path(dk.__file__).read_text(encoding="utf-8")
        assert "probability" not in src.lower()
        assert "stochastic" not in src.lower()

    def test_only_authorities_hard_veto_or_soft_policy(self) -> None:
        src = Path(dk.__file__).read_text(encoding="utf-8")
        assert "AuthorityLevel.HARD_VETO" in src
        assert "AuthorityLevel.SOFT_POLICY" in src
        assert "STRONG_CAUTION" not in src
        assert "WEIGHTED_VOTE" not in src
