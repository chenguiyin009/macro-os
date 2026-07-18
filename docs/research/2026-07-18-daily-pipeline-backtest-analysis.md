# Macro OS 日频全管线回测分析报告

**回测日期**: 2026-07-18
**评估窗口**: 2024-10-01 至 2026-07-17（日频 / 交易日）
**交易日数**: 468
**关联提案**: ZIRP 陷阱拆除 v2.0 + 阶梯式权限映射（解除二元否决过度杀伤）

---

## 1. 执行摘要

本回测将月度全管线验证升级为**日频**，以捕捉月内尾部事件（VIX 脉冲、HY 利差跳变）。
数据来源：VIX/TIPS/名义10Y/HY 为 FRED 真实日线，DXY 为 FRED DTWEXBGS 真实日频经月度 ICE 锚点重定基（废除线性插值平滑）。

### 核心结果

- **交易日数**: 468
- **风险预算均值**: 0.3500
- **风险预算范围**: [0.00, 0.50]
- **零预算交易日**: 36/468 (7.7%)
- **非零预算交易日**: 432/468 (92.3%)
- **权限层分布**: {'SAFETY_GATE': 432, 'HARD_VETO': 36}
- **状态分布**: {'TRANSITION': 205, 'TIGHT_LIQUIDITY': 227, 'LIQUIDITY_SQUEEZE': 36}

**关键验证点**：与月度回测（20 个月中仅 2 个月归零）一致，日频下零预算仅出现在真正的
尾部危机日（VIX≥25 或 HY≥400bp 触发 LIQUIDITY_SQUEEZE，或物理红线 VIX≥40/HY≥600bp）。
TIGHT_LIQUIDITY / TRANSITION 交易日均获得 0.10-0.40 的阶梯弹性预算，二元否决已彻底解除。

## 1.5 DXY 平滑偏差修正前后对比（v1 插值 → v2 真实日频）

- **风险预算均值**: 0.3541 → 0.3500
- **风险预算零日占比**: 7.7% → 7.7%

| 状态 | v1(插值) 天数 | v2(真实) 天数 | 变化 |
|------|--------------|--------------|------|
| LIQUIDITY_SQUEEZE | 36 | 36 | +0 |
| TIGHT_LIQUIDITY | 221 | 227 | +6 |
| TRANSITION | 211 | 205 | -6 |

- **DXY 日变动波动率 (std of ΔDXY)**: 0.0918 → 0.3195
  （真实日频 DXY 的日间跳变显著大于线性插值，验证平滑偏差已消除）

> 注：DXY 量纲经月度 ICE 锚点重定基，水平阈值不变；尾部拦截由 VIX/HY 红线主导，
> 故预算均值/零日占比与 v1 基本一致，差异主要体现在日间美元波动形态与过渡期概率分布。

---

## 2. 各状态的风险预算分布

| 状态 | 交易日数 | 预算均值 | 预算区间 |
|------|---------|---------|---------|
| LIQUIDITY_SQUEEZE | 36 | 0.000 | [0.00, 0.00] |
| TIGHT_LIQUIDITY | 227 | 0.291 | [0.10, 0.30] |
| TRANSITION | 205 | 0.477 | [0.10, 0.50] |

---

## 3. 尾部风险日（零预算 / 红线触发）

共 36 个尾部日。按 VIX 降序列出前 15：

| 日期 | VIX | HY(bp) | DXY | TIPS% | 状态 | 预算 | 红线/原因 |
|------|-----|--------|-----|-------|------|------|----------|
| 2025-04-08 | 52.3 | 457 | 103.8 | 2.04 | LIQUIDITY_SQUEEZE | 0.00 | vix_escape_hatch |
| 2025-04-07 | 47.0 | 461 | 103.8 | 1.96 | LIQUIDITY_SQUEEZE | 0.00 | vix_escape_hatch |
| 2025-04-04 | 45.3 | 445 | 103.2 | 1.83 | LIQUIDITY_SQUEEZE | 0.00 | vix_escape_hatch |
| 2025-04-10 | 40.7 | 442 | 102.3 | 2.21 | LIQUIDITY_SQUEEZE | 0.00 | vix_escape_hatch |
| 2025-04-11 | 37.6 | 426 | 101.3 | 2.28 | LIQUIDITY_SQUEEZE | 0.00 | VETO_REGIME_SQUEEZE_ACTIVE |
| 2025-04-21 | 33.8 | 416 | 99.4 | 2.20 | LIQUIDITY_SQUEEZE | 0.00 | VETO_REGIME_SQUEEZE_ACTIVE |
| 2025-04-09 | 33.6 | 437 | 103.6 | 2.07 | LIQUIDITY_SQUEEZE | 0.00 | VETO_REGIME_SQUEEZE_ACTIVE |
| 2025-04-16 | 32.6 | 416 | 100.5 | 2.12 | LIQUIDITY_SQUEEZE | 0.00 | VETO_REGIME_SQUEEZE_ACTIVE |
| 2026-03-27 | 31.1 | 342 | 104.7 | 2.13 | LIQUIDITY_SQUEEZE | 0.00 | VETO_REGIME_SQUEEZE_ACTIVE |
| 2025-04-14 | 30.9 | 414 | 100.9 | 2.15 | LIQUIDITY_SQUEEZE | 0.00 | VETO_REGIME_SQUEEZE_ACTIVE |
| 2026-03-30 | 30.6 | 346 | 104.6 | 2.04 | LIQUIDITY_SQUEEZE | 0.00 | VETO_REGIME_SQUEEZE_ACTIVE |
| 2025-04-22 | 30.6 | 399 | 99.5 | 2.14 | LIQUIDITY_SQUEEZE | 0.00 | VETO_REGIME_SQUEEZE_ACTIVE |
| 2025-04-15 | 30.1 | 409 | 100.9 | 2.16 | LIQUIDITY_SQUEEZE | 0.00 | VETO_REGIME_SQUEEZE_ACTIVE |
| 2025-04-03 | 30.0 | 401 | 102.3 | 1.78 | LIQUIDITY_SQUEEZE | 0.00 | VETO_REGIME_SQUEEZE_ACTIVE |
| 2025-04-17 | 29.6 | 402 | 100.3 | 2.11 | LIQUIDITY_SQUEEZE | 0.00 | VETO_REGIME_SQUEEZE_ACTIVE |

---

## 4. 状态转移矩阵（日→日）

| From \ To | LIQUIDITY_SQUEEZE | TIGHT_LIQUIDITY | TRANSITION |
|-----------|-----------|-----------|-----------|
| LIQUIDITY_SQUEEZE | 25 (69%) | 7 (19%) | 4 (11%) |
| TIGHT_LIQUIDITY | 9 (4%) | 214 (94%) | 4 (2%) |
| TRANSITION | 2 (1%) | 6 (3%) | 196 (96%) |

---

## 5. 与月度回测的对比

| 维度 | 月度回测 | 日频回测（本报告） |
|------|---------|-------------------|
| 样本点 | 20 个月 | 468 个交易日 |
| 粒度 | 月均值/月末值 | 真实交易日 |
| 尾部捕捉 | 平滑掉月内 spike | 捕捉 VIX 日内脉冲、HY 跳变 |
| 零预算占比 | 2/20 (10%) | 见上 |
| 预算范围 | [0.00, 0.40] | [0.00, 0.50] |

---

## 6. 已知局限性

- **DXY 现已真实日频（v2 修正）**：FRED DTWEXBGS 日线经月度 ICE 锚点重定基，废除线性插值，
  日内/日间美元跳变（如套利交易逆转）现被如实捕捉；量纲仍锁定 100-108 以兼容冻结阈值。
- **无独立美元跳变红线**：当前物理红线仅含 VIX/HY/core_pce，DXY 仅经 60d z-score 与水平阈值间接参与；
  若启用 dxy_zscore_60d_max 相对路径，真实日频 DXY 将立即提供美元动量信号（此前被插值抹平）。
- **core_pce 缺失**：物理红线 core_pce_max 未参与（PCE 仅季度）。
- **TIPS ROC 早期空窗**：窗口前 60 个交易日 ROC 未定义，RISK_ON 相对路径早期不触发（无碍，高利率期本不 RISK_ON）。
- **无交易成本**：risk_budget 为理论配置上限，未扣摩擦。
- **样本量**：约 430 交易日，仍符合参数冻结条款（N<100 交易样本指实盘，回测仅验证逻辑）。

> ⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。