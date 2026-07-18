# Macro OS 宏观状态机说明 (v2.0)

> 2026-07-18 — ZIRP 陷阱拆除重构

## 背景

旧版 RISK_ON 判定使用 `tips_yield_max: 0.5` 作为绝对阈值。这是 2010-2021 零利率时代 (ZIRP) 的遗留产物。在 2026 年真实利率 2.3% 的环境下，该阈值导致系统**永远无法进入 RISK_ON**。

## 新逻辑：基于边际变化的相对估值体系

### Gate 1: 硬天花板 (Hard Ceiling)

```
TIPS > 2.5% → 一票否决，强制判定为非 RISK_ON
```

用于防范恶性通胀和系统性紧缩。即使在信号 B/C 满足的情况下，只要 TIPS 绝对水平超过天花板，RISK_ON 就被阻断。

### Gate 2: 边际变化信号计数

| 信号 | 条件 | 含义 |
|------|------|------|
| A: 60日变化率 | ROC < -15% | 真实利率在短期内快速下行（边际宽松） |
| B: 2年滚动分位 | percentile < 25% | 真实利率处于历史相对低位 |

满足任一信号即可触发。当 `min_signals_required = 1` 时，只需 1 个信号；未来样本量 N > 100 后可收紧至 2。

### Gate 3: 最终判定

```
signals >= min_signals_required → RISK_ON
```

## 配置 (`config/thresholds.yaml`)

```yaml
regime:
  risk_on:
    tips_absolute_max: 2.5
    tips_roc_60d_threshold: -0.15
    tips_percentile_2y: 0.25
    min_signals_required: 1
```

## 冻结条款 (Anti-Overfitting Freeze)

在真实环境交易样本数 N < 100 之前，以上系统参数绝对冻结，禁止通过回测曲线拟合进行参数调优。此条款遵循 `backtest-factor-optimization-sop.md` 的 Occam's Razor 原则。

## 模块位置

- **新模块**: `core/macro_state/regime.py`
  - `_determine_risk_on(tips_yield, tips_history, config)` — 核心门控函数
  - `determine_regime(features, tips_history, config)` — 主入口
- **旧模块**: `core/regime.py` — 保持不变，仍由 `replay_engine.py` 调用
- **测试**: `tests/test_regime.py` — `TestRiskOnZIRP` 类（4 个测试）

## 与旧系统的关系

新模块是**互补**而非替代。旧 `compute_regime(features, config)` 是无状态函数，不需要历史数据。新 `_determine_risk_on` 需要 `tips_history: pd.Series`，在特征管线补齐 TIPS 历史数据后可接入。

## 设计亮点

1. **硬天花板保留**：引入相对估值的同时保留 2.5% 绝对红线，防止恶性通胀环境下的误判
2. **信号计分制**：`signals += 1` 设计支持未来平滑收紧（`min_signals_required: 1 → 2`）
3. **参数冻结条款**：防范宏观过拟合的最强武器
4. **无 scipy 依赖**：2 年分位计算用纯 numpy 实现
