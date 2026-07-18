# Macro OS v5.1 Phase 1 + P0-2 日频回测验证报告

**日期**: 2026-07-18
**评估窗口**: 2024-10-01 → 2026-07-17（468 交易日，日频）
**对照基线**: `pipeline_backtest_daily_v2_baseline.*`（真实 DXY 重定基、无 HY 因子、无危机梯度）
**本轮产物**: `pipeline_backtest_daily_v3.csv/.json` + 本分析

---

## 1. 执行摘要

按你的三项选择（DXY 保持冻结 / 危机梯度重做到 `decision_kernel.py` / HY 因子做进 CSI 并回测验证），本轮落地了三处代码改动并重跑日频全管线。结论：

- **HY 信用因子确实缓解了 TIGHT 偏差**，且方向正确（此前数据校验：HY 中位数 287，87.8% 交易日 HY≤320，信用宽松是常态，加正权项把 CSI 往下拉）。
- **危机降级梯度补上了"硬零→硬跳"的缺口**，但实现时发现并修复了你原提案里的两个 bug（梯度层级顺序、同日粘性锁冲突）。
- **DXY_CENTER 维持冻结**（Directive 3）。验证显示 HY 单独就把 TIGHT 从 227 天压到 14 天，已无必要再动 DXY 中枢——你选项 q-0「先验证再决定」的结论是：**DXY 不调**。

> 本轮所有改动均为**新因子 / 新机制**，未触碰任何冻结参数（`DXY_CENTER=102.0`、`VIX_CENTER=20.0`、`tips_roc_60d_threshold` 等维持原值）。

---

## 2. 改动清单（落地位置）

| 改动 | 文件 | 说明 |
|------|------|------|
| P1 有效杠杆 | `core/regime.py` `compute_regime` | TIGHT_LIQUIDITY 门新增 `hy_tight_floor`（默认 320，config 驱动）：HY<floor（信用宽松）时即便 DXY≥103 且 TIPS≥0.8 也降级为 TRANSITION |
| P1 CSI 补全 | `core/probabilistic/softmax.py` | `compute_crisis_stress_index` 增加 HY 项（`W_HY=+0.15, HY_CENTER=320, /50`）；`compute_regime_probabilities` 透传 `hy_credit_spread` |
| P0-2 危机梯度 | `core/decision_kernel.py` | LIQUIDITY_SQUEEZE 分支内实现渐进再入场（VIX/HY 在包络内回落释放 0.05/0.10/0.20） |
| P0-2 粘性锁保护 | `core/decision_kernel.py` + `runtime/orchestrator.py` | 新增 `sticky_day_lock` 参数；同日红线锁定期间**禁用**梯度，防止日内 VIX 跳变重新打开敞口 |
| 配置 | `scripts/backtest_regime.py` CONFIG + `config/thresholds.yaml` | `regime.tight_liquidity.hy_tight_floor: 320.0` |

---

## 3. 回测结果：v2 基线 → v3（P1+P0-2）

| 指标 | v2 基线 | v3（本轮） | 变化 |
|------|--------:|-----------:|-----:|
| 风险预算均值 | 0.350 | **0.448** | +0.098 |
| 预算区间 | [0, 0.50] | [0, 0.50] | 不变 |
| 零预算日 | 36 (7.7%) | **6 (1.3%)** | −30 |
| 非零日 | 432 | 462 | +30 |
| TRANSITION | 205 | **418** | +213 |
| TIGHT_LIQUIDITY | 227 | **14** | −213 |
| LIQUIDITY_SQUEEZE | 36 | 36 | 0（红线驱动，不变） |

**解读**：
- SQUEEZE 天数不变（36），说明红线拦截未被削弱；减少的 30 个零预算日全部来自**危机梯度在 SQUEEZE 缓和尾部释放的渐进预算**。
- TIGHT 大幅下降是 P1 的 HY 宽松降级门所致（详见第 5 节敏感性）。

---

## 4. P0-2 危机降级梯度（含两处 bug 修复）

### 4.1 Bug 1 — 梯度层级顺序（已修复）

原逻辑按 `(35,420,0.05)→(28,380,0.10)→(22,340,0.20)` 顺序返回**首个匹配**。但三档是嵌套的（越严越窄），满足第 2/3 档必然也满足第 1 档，于是任何释放日都被卡在 0.05，0.10/0.20 是死代码。修复：取所有满足档中的**最高**预算（`max`）。

修复后释放分布：**0.10 → 18 天，0.05 → 12 天**，硬零仅 6 天（核心危机 VIX>35）。

### 4.2 Bug 2 — 同日粘性锁冲突（已修复，安全关键）

`test_same_day_sticky_lock_after_red_line` 要求：**红线触发的当天，即便 VIX 日内回落，预算仍须 0.00**（防 hatch chatter 重新打开敞口，ADR-001）。原实现在 VIX 从 45 跌到 20 的粘性锁日释放了 0.05，违反该契约。修复：新增 `sticky_day_lock` 参数，锁定期间禁用梯度。

### 4.3 真实 2025-04 关税冲击窗口（用真实 CSV，非你原表）

| 日期 | VIX | HY | 旧预算 | v3 预算 | 说明 |
|------|----:|---:|------:|--------:|------|
| 4/3 | 30.0 | 401 | 0.00 | **0.05** | 阶段一（VIX≤35,HY≤420）|
| 4/4 | 45.3 | 445 | 0.00 | 0.00 | 核心危机，保持防御 |
| 4/7 | 47.0 | 461 | 0.00 | 0.00 | 核心危机 |
| 4/8 | 52.3 | 457 | 0.00 | 0.00 | 核心危机 |
| 4/9 | 33.6 | 437 | 0.00 | 0.00 | **HY=437>420，阶段一不满足**（你原表写 0.05，错）|
| 4/10 | 40.7 | 442 | 0.00 | 0.00 | VIX>35 |
| 4/11 | 37.6 | 426 | 0.00 | 0.00 | VIX>35 |
| 4/14~4/22 | 30~33 | 399~416 | 0.00 | **0.05** | 阶段一 |
| 4/23 | 28.5 | 375 | 0.00 | **0.05** | VIX=28.45>28，阶段二不满足（你原表写 0.10，错）|
| 4/24 | 26.5 | 373 | 0.00 | **0.10** | 阶段二（修复后正确）|
| 4/25 起 | <25 | <394 | 0.10~0.20 | 0.15~0.25 | 已非 SQUEEZE（TRANSITION），正常预算 |

> 你原表的两处数值错误（4/9、4/23）源于把 HY/ VIX 阈值边界算反；真实数据如上。旧系统在 4/25 前已给 0.10–0.20（非"完全空仓"），新梯度在 4/3 即开始 0.05 试探。

---

## 5. P1 HY 因子：方向与幅度

HY 中位数 287、87.8% 交易日 ≤320 → 正权项（`W_HY=+0.15`）平时把 CSI 往下拉，仅在信用紧张日（HY>320，约 12%）推高危机概率。方向正确，验证通过。

**但幅度需你拍板**：`hy_tight_floor=320` 让 TIGHT 几乎消失（v3 实际 14 天）。阈值敏感性（满足 DXY≥103 & TIPS≥0.8 的天数共 249）：

| HY floor | TIGHT 候选天数 | 含义 |
|---------:|---------------:|------|
| 320（当前）| 26（实际 14）| 激进，TIGHT 近乎消失 |
| 300 | 68 | 中等缓解（227→68）|
| 340 | 11 | 极紧 |
| 360 | 4 | 几乎无 TIGHT |

**建议**：若希望 TIGHT 仍是有意义的状态（而非被 TRANSITION 吞没），把 `hy_tight_floor` 提到 **300** 更平衡（TIGHT≈68 天）。当前 320 偏激进——这是新因子阈值，不违反冻结纪律，但需你确认。

---

## 6. DXY_CENTER：维持冻结（q-0 结论）

验证证明 HY 单独已将 TIGHT 从 227 压到 14 天，预算中枢从 0.35 抬到 0.45。原提案中"DXY 中枢 102→99.5 缓解 TIGHT"的动机关键已被 HY 因子满足，且：
1. 该改动违反 Directive 3 冻结纪律；
2. 真实 rebased DXY 中位数 103.56（非你引用的 101.3）；
3. 方向算反——降中心会让 `(dxy-中心)` 更大、美元显得更强、TIGHT 反而增多。

**结论：DXY_CENTER=102.0 保持冻结，不动。**

---

## 7. 测试

- 全量 pytest：**646 passed, 1 failed**（仅 `test_yfinance_macro` 因环境缺 `yfinance` 包，与本次改动无关，历史既有）。
- 更新/新增：`test_decision_kernel.py`（核心危机 SQUEEZE 仍 0.0 + 渐进释放用例）、`test_boundary_chatter.py`（核心危机 0.0 + CASH_LIQUIDATION 永不被梯度放松）。
- 修复回归：`test_adr001_absolute_red_line::test_same_day_sticky_lock_after_red_line`（同日粘性锁期间梯度已被禁用）。

---

## 8. 交付文件

- `docs/research/pipeline_backtest_daily_v3.csv` / `.json`（本轮日频回测）
- `docs/research/2026-07-18-daily-pipeline-backtest-analysis-v3.md`（脚本生成报告）
- `docs/research/pipeline_backtest_daily_v2_baseline.csv` / `.json`（对照基线）
- 代码：`core/regime.py`、`core/probabilistic/softmax.py`、`core/decision_kernel.py`、`runtime/orchestrator.py`、`scripts/backtest_regime.py`、`config/thresholds.yaml`

> 注：规范交付名 `pipeline_backtest_daily.csv` 被预览面板锁定（早前 present 占用），本轮暂用 `_v3` 后缀；关闭预览后可重命名为规范名。

---

> ⚠️ 以上内容由 AI 基于公开信息整理生成，仅供参考，不构成任何投资建议或个股推荐。投资有风险，决策需谨慎。
