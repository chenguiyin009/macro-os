# Macro OS v5.0 | 回测与因子优化标准作业程序 (SOP)

- **日期**：2026-07-17
- **版本**：v5.0
- **目的**：规范如何从 daily-live-review 和回测报告中提取洞察，定位系统瓶颈，并安全地优化 L1~L4 的代码与特征权重，防止过拟合（Overfitting）
- **触发条件**：每次运行 `daily_live_report.py` 产出报告后，或执行大规模回测后

---

## 阶段一：阅读回测报告，发现"异常征兆" (Discovery)

每次运行大规模回测或查看日度 Review 时，不要只看最终的 PnL，请按以下顺序排查系统健康度：

### 1. 检查"损耗与摩擦" (Replay Metrics)

- **指标**：对比 `gross_pnl` (毛收益) 与 `net_pnl` (净收益)
- **病症**：如果 Gross 为正，但 Net 为负或大幅缩水，且 `trade_count` (交易频次) 极高
- **诊断**：系统存在"震荡损耗 (Whipsaw)"。通常发生在 SAFETY_GATE（恢复期）和 HARD_VETO（危机期）之间频繁横跳
- **代码优化方向**：引入防抖机制 (Idempotence/Debouncing)。在 Orchestrator 层增加"状态锁"（如 ADR-001 中讨论的 `red_line_day_lock`），限制单日内的预算反转次数

### 2. 检查"多重共线性" (Alpha Report - Interactions)

- **指标**：观察 `top_interactions` 中的拮抗效应（如 interaction: -0.0859）
- **病症**：两个正向因子（如 STRUCTURE 和 TOTAL_SCORE）同时触发时，收益反而不如单独触发
- **诊断**：因子冗余与过度共识。当所有指标都完美时，往往是拥挤交易（Crowded Trade）或量化骗线
- **代码优化方向**：在 L4 (Sizer) 或微观网关中做因子正交化或裁剪 (Pruning)。砍掉复合型的宽泛指标（如 TOTAL_SCORE），保留具有物理意义的纯粹指标（如 MA55_PULLBACK 或 DIVERGENCE）

### 3. 检查"状态机混淆度" (Confusion Accuracy)

- **指标**：`transition_accuracy` 和状态转移矩阵
- **病症**：系统将实际的 NARROW_LEADERSHIP (窄幅领涨) 误判为 FAST_LIQUIDITY_SHOCK (流动性冲击)
- **诊断**：`core/probabilistic/softmax.py` 中的先验权重 (W_VIX, W_DXY) 失真，或锚点 (VIX_CENTER) 未适应当前宏观环境（例如从零利率时代进入了 5% 高息时代）
- **代码优化方向**：利用宏观周报（Funding Price Quadrant）校准 Softmax 的中心锚点，或引入 Regime 转移的非线性激活阈值

---

## 阶段二：定位病灶，隔离测试 (Diagnosis & Isolation)

发现问题后，绝不能直接在 L3 Kernel 中拍脑袋修改代码。必须利用 Macro OS 的分层架构进行隔离测试。

### 1. 运用反事实影子引擎 (Shadow Engine)

如果你怀疑当前的宪法（`decision_kernel.py`）过于保守导致错失反弹，不要直接改参数。

- **行动**：在 `core/shadow_engine.py` 中新增一个 `test_hypothesis` 组合（例如：延迟 1 天触发 HARD_VETO）
- **验证**：跑一周回测，对比 baseline (当前宪法) 和 test_hypothesis 的 `max_drawdown` 和 `total_return`。如果新假设在未增加显著回撤的情况下提升了收益，才考虑合入主干

### 2. 区分"数据污染"与"逻辑错误"

如 2026-07-03 的 Review 所示，如果快照抓到了 `tips_yield: 0.6`（旧世界数据），那么内核判定错误是 L1 Data Layer 的锅，绝不是 L3 Kernel 的逻辑问题。

- **行动**：在排查 Kernel 代码前，先去 `config/field_mappings.yaml` 检查映射，去 `core/sensors` 检查 API 数据量纲是否正确

---

## 阶段三：安全的代码与因子更新 (Safe Optimization)

当确诊问题并准备修改代码时，严格遵循 Decision Authority Map v5.0：

### 优化场景 A：需要调整对某种危机的敏感度

- **错误做法**：在 `TrinityAdapter` 中加一个 `if danger_score > 80: sell()`
- **正确做法**：修改 `core/schemas.py` 中 `danger_score` 的计算权重，或者在 `core/decision_kernel.py` 中微调 `crisis_threshold`，让所有的下游（包含 CIO Agent 和网关）统一步调

### 优化场景 B：过滤微观的"假突破/假跌破"

- **错误做法**：让 Macro Kernel (L3) 去读取个股的 K 线形态
- **正确做法**：把微观因子的优化交给 RiskGateway (L3b 仅做 AND 过滤) 或者 FractureAwareSizer (L4)。如果 MA55_PULLBACK 因子的 Alpha 很高，应该在 L4 的资金分配中，给满足该形态的标的分配更高的子预算

### 优化场景 C：引入新的实盘经验（如"冰点救市"）

- **错误做法**：将 `GJD_support` 作为一个硬开关直接关掉 `HARD_VETO`
- **正确做法**：参照 `docs/capitulation-exemption-contract.md`。将其作为结构化信号写入 `FeatureSchema`，在 SAFETY_GATE 阶段作为不强平的佐证，并旁路输出到飞书和研报，永远保持物理红线的绝对性

---

## 阶段四：防范宏观过拟合 (Anti-Overfitting Rules)

在量化高频里，夏普 3.0 可能是印钞机；但在量化宏观里，回测夏普 3.0 绝对是未来函数的受害者。

### 1. 宏观体制切割 (Regime Slicing)

回测绝不能只跑一整段。必须把 2020（大放水）、2022（暴力加息）、2024（AI 狂热）切开测。一个好的宏观因子，必须在至少两种截然不同的 Regime 中证明其不造成灾难。

### 2. 逻辑推导优先于 P-Value

如果一个因子（如某韩国半导体指数）在回测中胜率 100%，但在经济学和资金传导链条上解释不通，必须弃用。宏观系统只交易具备物理/经济学因果关系的逻辑。

### 3. 参数最少化 (Occam's Razor)

不要去微调 `W_VIX` 是 0.50 还是 0.51。如果你的策略对参数微调极其敏感，说明它根本承受不住真实市场的噪音。宏观参数必须像"砍刀"一样粗犷（例如 0.2, 0.5, 0.8）。

---

## 附录：本次执行记录 (2026-07-17)

### 基于 2026-07-03 Daily Review 的因子权重调整

**数据来源**：`docs/research/2026-07-03-daily-live-review.json` 的 `alpha_report` 部分

| 因子 | 旧权重 | 新权重 | Alpha | 依据 |
|------|--------|--------|-------|------|
| STATE | 0.30 | 0.20 | — | 与 DIVERGENCE 拮抗 (-0.0696) |
| STRUCTURE | 0.25 | 0.20 | — | 与 TOTAL_SCORE 拮抗 (-0.0859) |
| MA55_PULLBACK | 0.20 | **0.30** | +0.0252 | 最高正向 Alpha |
| DIVERGENCE | 0.15 | **0.25** | +0.0141 | 次高正向 Alpha |
| TOTAL_SCORE | 0.10 | **0.05** | -0.0089 | 负 Alpha + 共线性源头 |

**修改文件**：
- `trinity/core/engine.py` — 生产引擎 `_build_factors` 权重
- `scripts/generate_alpha_report.py` — 归因报告两处 `_build_factors` 副本权重
- `tests/test_outcome_aggregator.py` — 测试 fixture `make_factors` 权重同步

### TIPS Mock 数据修复（B4，已完成）

`adapters/tradingview.py` 的 `_mock_snapshot` 已从 `tips_yield=0.6` 更新为 `tips_yield=2.32`，对齐 2026-07 周报 Q1 压力测试量级。DXY 从 104.5 调整为 101.12，HY credit spread 维持 320bp。

> ⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。
