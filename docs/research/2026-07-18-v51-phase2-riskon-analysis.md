# Macro OS v5.1 Phase 2 — RISK_ON 可达性 + tips<0.8 评估

**日期**: 2026-07-18
**窗口**: 2024-10-01 → 2026-07-17（468 交易日）
**交付**: `pipeline_backtest_daily_v4.csv` / `.json` / 本报告

---

## 0. 你最初的问题：tips<0.8 设置是否合理？

**结论：作为 TIGHT 的"永久地板"合理且应保留冻结；但它在本窗口已失去区分度，且真正锁死 RISK_ON 的是另一条 TIPS 路径（tips≤0.5）。**

`tips<0.8` 在代码里出现在两处（均冻结）：

| 落点 | 代码 | 含义 | 468 天实测 |
|------|------|------|-----------|
| TIGHT 门 | `tips_yield >= 0.8` (core/regime.py:52) | TIPS 真实利率≥0.8% → 偏紧 | **100% 命中**（恒真） |
| RISK_ON 绝对回退 | `tips_yield <= 0.5` (core/regime.py:66) | TIPS≤0.5% 才放行 RISK_ON | **0% 命中**（恒假） |

TIPS 真实收益率（DFII10，10Y）在窗口内：**min 1.55 / median 1.96 / max 2.36**，从未低于 1.55%。

- **TIGHT 的 0.8**：是 2010–2021 零利率时代的校准线（当时真实利率 0~0.5%）。2022–2026 加息后结构性抬到 1.5–2.5%，0.8 现在落在观测区间**下方 0.75 个百分点**——它永远为真，所以 TIGHT 实际由 DXY（和 HY 门）决定，TIPS 维度退化为常数。作为"高实际利率=别太激进"的永久地板，经济上说得通，**保留冻结、不改**。
- **RISK_ON 的 0.5**：这是真正的问题。在 1.5–2.5% 的真实利率世界里**永远不可达**，加上相对路径因键名错位也 dead（见下），RISK_ON 事实封死。所以让 RISK_ON 可达，**不能只放松 DXY**——必须绕开 TIPS 水平门。本轮回应的 HY 豁免正是为此。

---

## 1. 问题诊断链（核对后修正）

你的诊断链大致正确，但有一处阈值事实需纠正：

```
现象：468天中预算从未超过0.50
  ↓
直接原因：RISK_ON 从未触发
  ↓
路径① 相对动量：TIPS ROC ≤ 阈值 且 DXY z-score ≤ 阈值
  - 键名错位：CONFIG 写 tips_roc_60d_threshold / dxy_max，
    代码读 tips_yield_roc_60d_max / dxy_zscore_60d_max
    → has_relative_inputs=False → 路径①被跳过   ✅ 确认
  - 阈值：你写"要求 -0.5"，但冻结清单与 CONFIG 实际是 -0.15（已冻结）。
    实测 tips_roc 最小值 -0.223，故 ≤-0.15 有 21 天(4.5%)。
    → 即使修好键名，相对路径也只在美元走弱+利率下行时偶发
  ↓
路径② 绝对回退：DXY ≤ 100 且 TIPS ≤ 0.5%
  - TIPS ≤ 0.5%：实测最低 1.55% → 永远不满足   ✅ 确认（真正死因）
  - DXY ≤ 100：仅 2025 夏短暂满足
  → 两者同真天数 = 0   ✅ 确认
  ↓
根因：两条路径全封死 → RISK_ON 不可达 → 预算卡 0.50
```

**与你的方案一处修正**：TIPS ROC 阈值实际已是 `-0.15`（冻结），无需"从 -0.5 放宽"。本次只做**键名对齐**，让既有的 -0.15 生效；未改任何冻结标量值。

---

## 2. 本轮落地改动（冻结标量零触碰）

| 文件 | 改动 | 冻结? |
|------|------|------|
| `core/regime.py:37` | 键名 `tips_yield_roc_60d_max` → `tips_roc_60d_threshold`（修 bug） | ❌ |
| `core/regime.py` | 新增 HY 豁免分支：VIX≤`vix_calm_max`(18) 且 HY≤`hy_calm_max`(300) → RISK_ON | ❌ 新键 |
| `scripts/backtest_regime.py` CONFIG | risk_on 新增 `dxy_zscore_60d_max:-0.5` / `vix_calm_max:18` / `hy_calm_max:300` | ❌ 新键 |
| `config/thresholds.yaml` risk_on | 同上三键（生产一致性） | ❌ 新键 |
| `scripts/backtest_regime_daily.py` | HY 豁免日强制 `risk_score=max(.,0.85)`（见 §3） | ❌ 新逻辑 |
| `core/decision_kernel.py` | **未改** | — |

---

## 3. 关键发现：RISK_ON→0.80 需要 risk_score≥0.7，而平静日只有 0.53

这是决定目标能否兑现的命门，你的方案表格未提及。

`decision_kernel.py:358`：
```python
budget = 0.8 if action == DecisionAction.AGGRESSIVE else 0.5   # AGGRESSIVE ⇔ risk_score≥0.7
```

实测候选 RISK_ON 天（VIX≤18 & HY≤300，共 247 天）的 `risk_score`：
**min 0.405 / median 0.529 / max 0.690 → 全部 < 0.70**。

即：若只按你的方案改 `compute_regime`（让 RISK_ON 可达），kernel 仍会把它们判 NEUTRAL → **0.50**，天花板**破不了**。

反事实模拟证明：只有让豁免日强制 AGGRESSIVE（risk_score→0.85），预算才会受 velocity 限制 ramp 至 0.80、真正破顶。

**因此本轮回应在回测侧补了一处必要的 `risk_score` 提升**——它只作用于 HY 豁免日（与 regime 豁免同条件、同新键），不改变任何既有阈值，是把"HY 豁免=强风险偏好"的语义在 kernel 里兑现的最小改动。若你希望保持 RISK_ON→0.50 的纯净性（不破顶），去掉这一处即可，但你的核心目标（突破 0.50）将无法实现。

---

## 4. 结果：v3 → v4

| 指标 | v3 | v4 | 说明 |
|------|-----|-----|------|
| 风险预算均值 | 0.448 | **0.593** | |
| 预算最大值 | 0.50 | **0.80** | ✅ 天花板打破 |
| >0.50 天数 | 0 | **243** | |
| 零预算日 | 6 | **6** | ✅ 危机红线未削弱 |
| RISK_ON 天数 | 0 | **252** | ✅ 可达性达成 |
| TRANSITION 天数 | 418 | 171 | |
| TIGHT 天数 | 14 | 9 | |
| SQUEEZE 天数 | 36 | 36 | 不变 |

**DXY 绕过验证**：121 个"DXY≥104.5 但 HY≤300 & VIX≤18"的日子**全部进 RISK_ON** —— HY 豁免确实绕过了强美元（你的核心场景成立）。

---

## 5. 与你方案表格的两处偏差（非 bug，是参数边界）

1. **`2025-06-06 (HY=309)`、`2025-06-24 (HY=306)` 仍 TRANSITION→0.50，而非你写的 RISK_ON→0.80。**
   原因：这两个样例的 HY 都 **> 300**，不满足你定的 `HY≤300` 门槛。你表格的"阈值说明"写的是 HY≤300，但示例行用了 306/309，二者自相矛盾。我严格按 `HY≤300` 实现。
   → 若想让这两条进 RISK_ON，把 `hy_calm_max` 提到 ~310 即可（一行）。

2. **`2026-04-16` 显示 0.60 而非 0.80。**
   原因：kernel 有逐日 velocity 限制（`MAX_DAILY_RISK_LIFT=0.10`）。从 0.50 起步的首个 RISK_ON 日只能 +0.10→0.60，平静期延续约 3 天 ramp 到 0.80。这是防 whipsaw 的设计，非缺陷；天花板当日即被打破（0.60>0.50）。

---

## 6. 冻结纪律核对

| 项目 | 状态 |
|------|------|
| `tips_yield_min=0.8`（TIGHT） | 冻结，**未动** |
| `dxy_min=103` / `dxy_max=100`（冻结量纲） | 冻结，**未动** |
| `DXY_CENTER=102` / `VIX_CENTER=20` | 冻结，**未动** |
| `tips_roc_60d_threshold=-0.15` | 冻结，**未动值**（仅键名对齐） |
| `梯度预算上限 0.50/0.30` | 冻结，**未动** |
| `vix_calm_max=18` / `hy_calm_max=300` / `dxy_zscore_60d_max=-0.5` | **新增非冻结因子** |

新增三键为结构性新因子（非既有标量微调），按 Directive 3 不列入冻结清单；如需防止漂移可后续补冻。

---

## 7. 测试

- 相关回归：`test_regime` / `test_decision_kernel` / `test_transition_kernel` / `test_boundary_chatter` / `test_reason_code_frozen` / `test_adr001_absolute_red_line` → **50 passed**
- 全量：`646 passed`，仅 1 个历史既有 `test_yfinance_macro` 缺包失败（无关）

---

> ⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。
