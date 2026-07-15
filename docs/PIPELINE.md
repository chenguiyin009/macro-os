---
tags: [macro-os, pipeline]
---

# Pipeline Reference

## Execution Order (HARD LOCKED)

```text
MCP / TradingView ??? PINE TABLE (10-call Macro Liquidity Composite)
                           ?
                           ?
                    FEATURE LAYER
                    (macro_mapper.py)
                           ?
                           ?
                    MACRO WORLD MODEL (v4.4)
                    ???????????????????????
                    ? compute_macro_state ? TIPS x DXY ? Quadrant
                    ? compute_confirmation? Gold x Credit ? DIVERGED
                    ???????????????????????
                              ?
                              ?
                    DIVERGENCE ENGINE (v4.5+v4.6)
                    ????????????????????????????????
                    ? compute_divergence_score      ? 4-axis crack detection
                    ? DivergencePhaseEngine         ? Multi-Front Resonance
                    ? PhaseHysteresisSmoother       ? Instant upgrade, delayed degrade
                    ????????????????????????????????
                              ?
                              ?
                    CONSTITUTIONAL KERNEL (v4.3.1)
                    ????????????????????????????????
                    ? 1. SAFETY_GATE               ? Divergence ? CRISIS ? RISK_REDUCE
                    ? 2. HARD_VETO                 ? TRANSITION/SQUEEZE/TIGHT ? REDUCE
                    ? 3. SOFT_POLICY               ? RISK_ON ? AGGRESSIVE/NEUTRAL/DEFENSIVE
                    ????????????????????????????????
                              ?
                              ?
                    EXPOSURE DAMPER (v4.6)
                    NonLinearExposureDampener
                    Sigmoid(k=12, x0=0.6)
                              ?
                              ?
                    ASSET SIZER (v4.6)
                    FractureAwareSizer ? watchlist.yaml
                    Moat/Logic/Catalyst ? targeted cuts
```

## Data Sources

| Source | Method | Data |
|--------|--------|------|
| TV MCP | quote_get | DXY, VIX, GOLD prices |
| TV MCP | data_get_ohlcv | QQQ close + volume |
| TV MCP | data_get_study_values | SOFR Path, HY Stress, VIX Stress, Real Yield |
| TV MCP | data_get_pine_tables | Macro Liquidity Composite (10-call) |
| Estimated | _feat_quality | HY spread when Pine unavailable |

## Key Constraints

- **No lookahead**: TemporalBuffer enforces strict(t) < event.t
- **No randomness**: Fully deterministic replay
- **No LLM in kernel**: Advisory-only
- **Macro First**: MacroState computed before any kernel logic

## Physical Red-Line Pre-Kernel Fold (v5.0)

物理红线（HARD_VETO 的硬约束）在 **kernel 之前**由编排层求值并折叠进 `hard_regime`，
kernel 本身保持 pure（不读 YAML / 不做 eval / 不碰 I/O）。

```text
build_features ──▶ evaluate_physical_red_lines(features, red_lines)  ──▶ hard_regime
                        ｜  red_lines 来自 config/thresholds.yaml                       ｜
                        ｜  constitution.red_lines (SSOT)                               ▼
                        ｜                                                  kernel_decide(features, hard_regime, ...)
                        ｜                                                                  ｜
                        └── 命中 → forced_hard_regime=LIQUIDITY_SQUEEZE ──▶ HARD_VETO (risk=0/def=1)
```

- **实现位置**：`core/macro/physical_red_lines.py`（纯函数），由 `runtime/orchestrator.py` 在
  `kernel_decide` 之前调用。`decide()` 的唯一入口地位与 pure 边界不受影响。
- **生效红线**：`vix_escape_hatch`(40)、`hy_credit_spread_bp`(bp，数百)、
  `core_pce_max`(**百分比** 3.5，与 `policy_engine` 同源)。
- **默认禁用**：`brent_red_line`。`brent_shock` 仅由 Pine 桥（CDP）注入，不在
  TV MCP → `build_features` 主路径，pre-kernel 阶段无数据源，启用会成“静默死规则”。
  重接方式见 `config/thresholds.yaml` 注释。
- **与 `policy_engine` 的边界**：本折叠负责 pre-kernel 的 `hard_regime`（命中即 HARD_VETO）；
  `policy_engine` 另在 allocation 通道对 `core_pce_max`/`vix_escape_hatch` 做二次约束，
  并独占 `danger(0–100) → 危机阈值` 通道。两者为纵深防御，非重复实现。
- **可观测性（主路径闭环，3 个编排层通道；内核 audit_trail 不动）**：触发时红线快照写入
  Event payload 顶层 `red_line` 键、飞书消息横幅、`health()` 的 `last_red_line_meta`。
  **内核 `KernelDecision.audit_trail` 四步 step key 刻意不被触碰**（review P0 #2：早期草案的
  `audit_trail["red_line"]` 附加键已移除，避免污染四步契约）。
- **已知量纲分歧**：`core/glen_red_lines.py` 历史用小数 `core_pce`（0.034/0.035），属独立子系统，
  待其 owner 统一到本系统的百分比标准。

## Step 5: Global Velocity Limit (v5)

The Global Velocity Limit acts as the final constitutional clamp after all budget authorities have settled. It prevents quantum leaps in risk exposure from one session to the next.

**Rule:** inal_risk_budget = min(candidate_risk_budget, previous_risk_budget + MAX_DAILY_RISK_LIFT)

When triggered, the eason_code field is set to GLOBAL_RAMP_ACTIVE.
