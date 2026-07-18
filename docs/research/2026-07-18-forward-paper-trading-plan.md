# Macro OS 前向纸盘测试方案（Forward Paper Trading Plan）

**日期**: 2026-07-18
**关联指令**: 审查报告第四节 Directive 2 — 启动前向纸盘测试（Initiate Forward Paper Trading）
**前置条件**: 本方案在 L1–L3 核心逻辑已完成历史闭环验证（见 `2026-07-18-daily-pipeline-backtest-analysis.md`）后启动；
所有参数与阈值按 `2026-07-18-parameter-freeze-manifest.md` **冻结**。

---

## 1. 目标

1. 在**样本外（OOS）** 环境累积 ≥ 100 个真实交易日信号样本，验证模型泛化能力。
2. 量化**真实交易摩擦**（买卖价差、滑点、税费）对净值的侵蚀，补足回测已知缺陷（审查 3.2）。
3. 持续监测状态机稳定性（审查 2.3 的高粘性是否延续至实盘频率）。

**非目标**：本阶段**不自动下单、不接入券商执行**。仅记录信号与预算，属纸盘（paper）。

---

## 2. 现有可复用资产（避免重复造轮子）

| 资产 | 路径 | 用途 |
|------|------|------|
| 收盘后复盘脚本 | `scripts/daily_live_report.py` | 按日期复用 ReplayEngine + alpha 报告，手动/定时可跑 |
| Pine 桥 | `relay/pine-bridge.mjs` | 从 TradingView 实时指标读取 study 值（已在 `_inject2.mjs` 体系验证） |
| 决策日志 | `vault/DECISION_JOURNAL.jsonl` | 既存信号落库结构，直接追加 |
| 收盘自动化 | 自动化任务 `每日收盘回测复盘分析`（16:30 港股收盘，工作日） | 触发调度 |
| 数据拉取 | FRED CLI（VIX/TIPS/10Y/HY 日线）+ 重定基 DXY | 特征源（同回测，保证一致） |

---

## 3. 每日纸盘循环（建议在 16:30 HK 收盘后触发）

```
1. 取数：FRED VIXCLS / DFII10 / DGS10 / BAMLH0A0HYM2（真实日线）
         + DTWEXBGS 重定基 DXY（与回测同源，零偏差）
2. 特征：复用 scripts/backtest_regime_daily.py 的 build_daily_feature_frame 单日切片
3. 管线：compute_regime_probs → evaluate_physical_red_lines → compute_regime → decide()
4. 信号：输出 {date, effective_hard_regime, risk_budget, defense_budget, kernel_reason_code, red_line_fired}
5. 落库：追加至 vault/DECISION_JOURNAL.jsonl（不自动执行）
6. 摩擦估算（后处理）：依据 budget 与前日变化估算换手，应用 cost model（见 §5）
7. 计数：累加真实交易日计数，达 100 触发 OOS 评审门
```

---

## 4. 样本量与门控

- **计数口径**：每个真正产生的交易日信号 +1（剔除数据缺失/休市日）。
- **门控**：`signed_days >= 100` 时，自动生成 OOS vs IS 对比摘要（预算分布、状态转移、尾部命中率），
  交架构审查决定是否进入"模拟撮合"或更高级部署。
- **早停红线**：若 OOS 期间物理红线（ADR-001）触发后次交易日未恢复中性（连续 HARD_VETO > 5 日且无缓和），
  触发人工复核，但**不自动改参**（参数冻结优先）。

---

## 5. 交易摩擦模型（补足审查 3.2）

回测 `risk_budget` 为理论配置上限。纸盘阶段引入后处理摩擦估算（不影响信号，只影响净值归因）：

- **换手**：`turnover_t = |budget_t - budget_{t-1}|`（预算变动近似调仓比例）
- **成本**：`cost_t = turnover_t * (half_spread + slippage_bps/1e4 + fee_bps/1e4)`
  - 默认 `half_spread = 0.05%`（股/ETF 近似），`slippage = 0.10%`，`fee = 0.02%`，可按资产类目覆盖
- **净权益**：在理论权益曲线上逐日扣 `cost_t`，输出 `net_of_friction` 曲线与回撤
- 仅在 OOS 阶段报告，IS 回测保持无成本以作对照

---

## 6. 守卫与终止

- 物理红线（ADR-001）在纸盘阶段**仍硬夺权**（HARD_VETO → 预算 0），与回测一致。
- 提供人工"一键暂停"开关（停止追加信号、停止摩擦估算），不依赖参数改动。
- 本方案本身**不修改任何冻结参数**；任何调参须回到 `parameter-freeze-manifest.md` 的解冻流程。

---

## 7. 交付物

- 每日：`vault/DECISION_JOURNAL.jsonl` 增量 + 可选 `docs/research/YYYY-MM-DD-paper-*.md` 周报
- 里程碑：`docs/research/<date>-oos-review.md`（N≥100 时）

---

> ⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。
