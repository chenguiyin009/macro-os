# Macro 系统 vs 资金价格周报：差距与改进计划

- **日期**：2026-07-16
- **对照样本**：`docs/research/2026-07-10-funding-price-weekly.md`（2026-07-06—07-10）
- **系统基线**：`main` 研究管线 + ADR-001 绝对红线
- **目标**：A 落盘周报；B 四象限研究层 → Feature/Regime 接线（不改 kernel 宪法）

---

## 1. 周报说了什么（系统必须能表达）

| 周报概念 | 含义 | 系统需要的表达 |
|----------|------|----------------|
| Q1 压力测试 | 名义↑ + 真实↑ | 研究象限标签 + hard_regime 提示 `TIGHT_LIQUIDITY` |
| 非美元荒/非信用危机 | DXY 未突破、HY 稳 | 不能仅因 TIPS 高就标 `LIQUIDITY_SQUEEZE` |
| 传导停在久期/估值 | 股指高β受伤，信用未爆 | 旁路叙事 + 可选 transmission_layer |
| 主导变量 | 10Y TIPS、30Y | 特征字段 `tips_yield`、`nominal_30y`（新增） |
| 快慢变量 | 增长/通胀/政策/供给 + 真实利率/美元/信用 | 研究快照 JSON，非 kernel 输入 |

---

## 2. 差距清单（按严重度）

### P0 — 叙事与执行脱节

1. **mock 快照与本周现实相反**  
   `TradingViewAdapter._mock_snapshot` 使用 `tips_yield=0.6`、`dxy=104.5`、`vix=18.2`。  
   相对 `macro_mapper` 的基准（tips 0.5 / dxy 100）会给出偏紧象限，但**量级不是 2.3% 真实利率世界**；且 dry-run 曾落到 `SAFETY_CRISIS`，与周报「信用稳、非系统性挤兑」的风险叙事不一致（数据陈旧/mock 放大结构相位）。

2. **四象限研究语言不存在**  
   周报 Q1–Q4（真实×名义）≠ 代码 `Quadrant`（TIPS trend × DXY trend 简化平面）。缺少：
   - `Q1_STRESS_TEST / Q2_DEBT_DEFLATION / Q3_POLICY_EASE / Q4_REFLATION`
   - 名义利率轴（10Y/30Y）
   - BEI / 曲线代理

3. **live 数据链断裂**  
   relay 日志 stale；MCP 路径未接本周表字段。系统无法自动复述周报，只能人工对照。

### P1 — 特征与映射缺口

| 周报字段 | FeatureSchema | build_features | 备注 |
|----------|---------------|----------------|------|
| 10Y TIPS | `tips_yield` 有 | 有 | mock 量级错误 |
| 10Y 名义 | **无** | 无 | 需 `nominal_10y` |
| 30Y 名义 | **无** | 无 | 需 `nominal_30y`（主导变量） |
| 2Y | **无** | 无 | 可选 `nominal_2y` |
| 10Y BEI | **无** | 无 | 可选 `bei_10y` |
| DXY | 有 | 有 | mock 可对齐 |
| HY 代理 | `hy_credit_spread` 有 | 有 | 周报用稳定利差；squeeze 阈值 400bp |
| MOVE/VIX | VIX 有；MOVE 无 | 部分 | 非本周主导 |

`macro_mapper._feature_trends` 用固定锚点 `tips-0.5`、`dxy-100`，对 **2.3% TIPS** 会永远给出巨大 tips_trend，缺乏「变化率/分位」语义，与周报「高位再紧缩但信用稳」不完全同构。

`regime.py` 的 TIGHT 门槛 `tips_yield_min: 0.8` 与真实 2%+ 世界兼容，但 **RISK_ON 绝对门槛 tips≤0.5** 在当前利率体制下几乎不可达；相对动量路径依赖 `tips_yield_roc_60d` / `dxy_zscore_60d`，mock 常缺失。

### P1 — 输出层

- CIO 日报模板是 allocation 导向，不是「机制 + 快慢变量 + 条件概率」周报结构。
- Event payload 无 `funding_price_quadrant` / `research_week` 引用。

### P2

- 无周度研究快照加载器；无 vault 周报索引。
- STATUS.md 仍写 v2.0 mock 时代。

---

## 3. 改进原则（合宪）

1. **研究层可扩展，kernel 不膨胀**：四象限计算 pure；只通过 `hard_regime` 提示与 Event/CIO 旁路输出。  
2. **禁止周报人工改预算**：不写 `decide()` 旁路。  
3. **量纲诚实**：TIPS/名义用百分点；HY 用 bp。  
4. **mock 可复现本周叙事**：开发默认快照应对齐最近研究周，而不是 0.6% TIPS 假世界。

---

## 4. 本迭代落地范围（MVP）

| ID | 项 | 状态目标 |
|----|----|----------|
| A1 | 周报 Markdown + JSON 快照落盘 | 本 PR |
| A2 | 差距与计划文档 | 本 PR |
| B1 | `FundingPriceQuadrant` 枚举 + pure 分类器 | 本 PR |
| B2 | Feature 增加 nominal_10y/30y/2y、bei_10y + 透传 | 本 PR |
| B3 | research 象限 → hard_regime hint 映射（不改 kernel） | 本 PR |
| B4 | mock 快照对齐 2026-07 周报量级 | 本 PR |
| B5 | CIO 可选渲染 funding-price 研究块 | 本 PR |
| B6 | orchestrator dry-run/main payload 挂 research 块 | 本 PR |
| B7 | 单测：周报 Q1 样本 → TIGHT hint 且非 squeeze | 本 PR |
| C1 | 实时 TV/FRED 拉数进 FeatureSchema | **本迭代：FRED CSV fallback + TV 链** |
| C2 | Event hydration of previous_risk_budget | **本迭代：session_hydration + orchestrator** |
| C3 | 完整周报自动生成流水线 | **本迭代：scripts/generate_funding_price_weekly.py** |

---

## 5. 映射规则（B 的规范）

### 5.1 研究四象限（真实利率方向 × 名义利率方向）

| 真实 \ 名义 | 名义下行 | 名义上行 |
|-------------|----------|----------|
| 真实上行 | Q2 债务通缩风险 | **Q1 压力测试** |
| 真实下行 | Q3 政策宽松/分母释放 | Q4 再通胀 |

方向由 **短窗变化**（优先 5d bp；否则 today Δ；否则相对温和锚点）决定，避免把「水平高」直接等同「正在上行」。

### 5.2 → Macro hard_regime hint

| 研究象限 | hard_regime_hint | 说明 |
|----------|------------------|------|
| Q1 | `TIGHT_LIQUIDITY` | 久期/真实资金成本压力 |
| Q2 | `TIGHT_LIQUIDITY` 或保持 phase 主导 | 更危险；仍非自动 squeeze |
| Q3 | `RISK_ON` hint only if credit/usd ok | 研究层提示，kernel 仍看 phase |
| Q4 | `TRANSITION` | 名义热、真实松 |

**显式非映射**：Q1 **不**映射为 `LIQUIDITY_SQUEEZE`，除非 VIX/HY 物理红线或 squeeze 规则单独触发。

### 5.3 orchestrator 接线

```text
features = build_features(raw)
research = classify_funding_price_quadrant(features)  # pure
# hard_regime_raw 仍来自 compute_macro_state / 可被 research hint 标注
# 不覆盖 red-line absolute fold；research 只写入 payload + CIO
```

MVP：**research 结果进入 payload/CIO**，`hard_regime` 主路径仍以 `compute_macro_state` 为准；同时提供 `research_hard_regime_hint` 供对照。可选开关 `USE_RESEARCH_QUADRANT_HINT=1` 时，在无红线触发下用 hint 替换 raw quadrant（测试覆盖）。

默认 **开启 hint 融合（轻量）**：当 research 置信度高且与 credit/usd 一致时，将 `hard_regime_raw` 设为 hint——以便 dry-run 在周报 mock 下对齐 Q1→TIGHT。若 red-line absolute，仍走 ADR-001。

---

## 6. 验收

1. 仓库存在本周周报 md + json。  
2. 加载周报 JSON/特征样本 → 分类为 `Q1_STRESS_TEST`。  
3. mock 快照 dry-run 的 research 块显示 Q1，且 `research_hard_regime_hint=TIGHT_LIQUIDITY`。  
4. 信用/美元稳定样本 **不会** 仅因 Q1 变成 `LIQUIDITY_SQUEEZE`。  
5. 相关单元测试绿；kernel 测试零回归。

## 7. Follow-up implemented (2026-07-16)

1. `adapters/fred.py` — FRED public CSV levels/5d bp; TV fetch chain MCP→relay→FRED→mock
2. `core/session_hydration.py` + orchestrator boot hydrate from vault RESEARCH_REPORT
3. `scripts/generate_funding_price_weekly.py` + `make weekly-report` / `weekly-report-mock`
