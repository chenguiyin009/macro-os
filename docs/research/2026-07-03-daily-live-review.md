# 每日实盘回测分析日报 -- 2026-07-03

- **报告日期**: 2026-07-03
- **生成时间**: 2026-07-18T01:26:22.304506Z
- **Schema**: `macro-os.daily-live-review.v1`

## 数据覆盖

| 指标 | 值 |
|------|-----|
| 当日事件数 | 1 |
| 当日日志条目 | 3 |
| Vault 总事件数 | 2 |
| 事件类型分布 | DECISION: 1 |

## 当日决策

| 时间 | Action | Regime | Risk Score | Confidence | Source |
|------|---------|--------|------------|------------|--------|
| 2026-07-03T18:51:06.786770 | LONG | TRANSITION | 0.6636533202575567 | 0.6636533202575567 | MOCK |

## 决策日志摘要

| 时间 | Action | Budget | Composite Regime | Danger Regime | Danger Score | QQQ | VIX | DXY |
|------|---------|--------|------------------|---------------|--------------|-----|-----|-----|
| 2026-07-03T11:30:00Z | NEUTRAL | 0.75 | WATCH | CAUTION | 2 | 495.0 | 18.5 | 104.5 |
| 2026-07-03T14:00:00Z | NEUTRAL | 1.0 | NEUTRAL | NEUTRAL | 0 | 520.0 | 12.0 | 100.0 |
| 2026-07-03T15:00:00Z | RISK_REDUCE | 0.0 | CRISIS | FAST_LIQUIDITY_SHOCK | 5 | 470.0 | 32.5 | 108.0 |

## 宏观特征快照

| 特征 | 值 |
|------|-----|
| dxy | 104.5000 |
| equity_tech_rotation | 0.1500 |
| gold | 2350.0000 |
| hy_credit_spread | 320.0000 |
| tips_yield | 0.6000 |
| vix | 18.2000 |

## 回测引擎指标 (ReplayEngine)

| 指标 | 值 |
|------|-----|
| Transition Accuracy | 100.0% |
| Stability Score | 1.0000 |
| Gross PnL | +0.0000 |
| Net PnL | -0.0008 |
| Sharpe | 0.0000 |
| Total Costs | 8.00 bps |
| Trade Count | 1 |
| Confusion Accuracy | 100.0% |

> 注: ReplayEngine 在全量事件流上运行, 指标为累计值而非当日单独值。

## Alpha 归因报告

- **样本数**: 15
- **平均收益**: +7.3328%
- **胜率**: 100.00%

### 因子 Alpha 排名

| 因子 | Alpha |
|------|-------|
| MA55_PULLBACK | +0.0252 |
| DIVERGENCE | +0.0141 |
| STATE | -0.0121 |
| TOTAL_SCORE | -0.0089 |

### 交互效应

- **STRUCTURE x TOTAL_SCORE**: -0.0859 -- 拮抗效应 (interaction=-0.0859): STRUCTURE与TOTAL_SCORE同时高位反而不如单独有效
- **DIVERGENCE x STATE**: -0.0696 -- 拮抗效应 (interaction=-0.0696): DIVERGENCE与STATE同时高位反而不如单独有效
- **MA55_PULLBACK x TOTAL_SCORE**: -0.0559 -- 拮抗效应 (interaction=-0.0559): MA55_PULLBACK与TOTAL_SCORE同时高位反而不如单独有效

### 权重优化建议

1. 增加 MA55_PULLBACK 权重 (Alpha=+0.0252, 正向贡献)
2. 增加 DIVERGENCE 权重 (Alpha=+0.0141, 正向贡献)
3. 降低 STATE 权重 (Alpha=-0.0121, 负向贡献)

---

本文件由 `scripts/daily_live_report.py` 自动生成。
回测结果为模型驱动, 不构成任何交易信号。

> WARNING: 以上内容由 AI 基于公开信息整理生成, 仅供参考, 不构成任何投资建议或个股推荐。投资有风险, 决策需谨慎。