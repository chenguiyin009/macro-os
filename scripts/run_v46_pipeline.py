import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.divergence.divergence_engine import DivergencePhaseEngine
from core.exposure_dampener import NonLinearExposureDampener
from core.fracture_aware_sizer import FractureAwareSizer, TargetDimension
from core.regime import compute_regime
from core.decision_kernel import decide as kernel_decide
from core.scoring import score
from config.settings import settings

config = settings.thresholds.model_dump() if settings.thresholds else {}
features = {"dxy": 100.5, "vix": 15.6, "tips_yield": 1.82, "hy_credit_spread": 300,
    "gold": 3950, "equity_tech_rotation": 0.15, "volume": 42121829, "volume_20d_avg": 47353453}

engine = DivergencePhaseEngine(use_pine_data=True)
ds = engine.compute_state(features, vix=features["vix"])
dampener = NonLinearExposureDampener()
exposure = dampener.calculate_max_exposure(ds.score, ds.phase)
hr = compute_regime(features, config)
r, cval, reason = score(features, hr, config)
kd = kernel_decide(features, hr, "TRANSITION", r, cval, config, divergence_phase=ds.phase)

sizer = FractureAwareSizer()
for t, ms, mo, ls, cat in [("QQQ",["RATES"],0.6,0.5,True),("HYG",["CREDIT"],0.6,0.7,False),("TLT",["RATES"],0.8,0.9,False)]:
    sizer.targets[t] = TargetDimension(t, ms, mo, ls, cat)
base = {"QQQ": 0.4, "HYG": 0.4, "TLT": 0.2}
adj = sizer.adjust_weights(base, ds.fractures, ds.phase)

print("=" * 72)
print("  MACRO OS v4.6 - FULL FRACTURE MAP (Pine Data Bridge)")
print(f"  Score: {ds.score:.4f} | Phase: {ds.phase} | Fractures: {' | '.join(ds.fractures)}")
print(f"  Max Exposure: {exposure:.4f}")
print(f"  Kernel: {kd.authority.value} -> {kd.decision.action.value} (Budget: {kd.risk_budget:.0%})")
print(f"  Sizer: Base={base} -> Adjusted={adj}")
print("=" * 72)
