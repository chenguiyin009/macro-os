---
tags: [macro-os, design, sentiment, shadow-factors, feishu, cn-tech, cycle]
status: draft-for-review
date: 2026-07-21
version: "2.0"
related:
  - docs/design/a_share_crash_analysis_summary_2026-07-21.md
  - docs/2026-07-21-cn-tech-sentiment-shadow-design.md
---

# A股情绪因子观测指标体系设计（V2.0 — 完整周期框架）

**Macro OS · CN Tech Sentiment Shadow Layer**  
Owner: Macro OS v5.0  
Status: Draft for architecture review  
Scope: **Observation only** — 不修改 Decision Kernel / 不改变 `risk_budget`

> 目标：把情绪因子从「单日反转触发器」升级为**贯穿科技回落完整周期的定位器**——回答：  
> **当前处于周期哪一段（高点回落 / 下行 / 摸底 / 磨底 / 反弹）？反弹是交易性还是中周期？救援流动性传到哪一环？**  
> 而不是只回答「今天能不能抄」。

---

## 0. 文档关系

| 文档 | 角色 |
|---|---|
| **本文** `docs/design/a_share_sentiment_factor_design.md` | **V2.0 SSOT**（周期 + 五维因子 + 微观危机指标 + 飞书/输出） |
| `docs/design/a_share_crash_analysis_summary_2026-07-21.md` | 07-17~21 复盘摘要（校准样本） |
| `docs/2026-07-21-cn-tech-sentiment-shadow-design.md` | V1.1 旁路架构与飞书卡底稿（被本文吸收并升级） |

V1.1 仍有效的部分：旁路架构、Feishu 观测卡、禁止进 Kernel、3750 锚、rescue ladder。  
V2.0 **新增**：全球周期 t0、五阶段 S0–S4、ARS 定位器、融资 Top100 微观、指数传导、托而不举模式。

---

## 1. 框架升级说明（相对 V1 / V1.1）

### 1.1 用户关键反馈

> 整体科技回落应从 **META 卖硬件事件** 起算；美股、韩国半导体、A股半导体均出现大幅度回撤；  
> 情绪因子应覆盖 **高点回落 → 低点摸底 → 反弹** 完整周期，而不是单日冰点开关。

### 1.2 三点升级

1. **周期视角**：全球科技同步回落周期（美/韩/A），t0 = META 相关硬件冲击见顶日（以数据校准，不写死记忆涨跌幅）。  
2. **因子 = 定位器**：持续输出阶段标签 + 切换信号 + 反弹性质，而非一次性抄底触发。  
3. **微观危机层**：07-17~21 证明必须有有融资跌停/开板、ETF 托底、大小盘传导，否则「温度计」解释不了流动性挤兑。

### 1.3 本周复盘注入的工程约束

| 复盘结论 | 设计约束 |
|---|---|
| 爆杠杆 + 跌停阻塞外溢 | 融资 Top100 跌停/开板/新增 为**一等公民** |
| GJD 托而不举 | `intervention_mode`，禁止把托底读成 V 反 |
| 300 → 创业板 → 1000 传导 | `liquidity_transmission_ladder` 状态机 |
| 右侧看拉起质量 | `bounce_quality` + 板块-指数共振 |
| 反弹 ≠ 反转 | `rebound_type` 强制字段 |
| 上证 3750 是底 | 可配置指数锚，观测不保证 |
| 先宽基后弹性 | `rescue_sequence_stage` 保留 |

---

## 2. Non-Goals / 原则

### 2.1 Non-Goals

1. 不改 `decision_kernel` / Hard Veto / risk_budget  
2. 不做自动挂单（-0.5%/-1%/-2% 仅为**人类纪律叙事**，可进 checklist 文案）  
3. 不输出个股买卖点引擎  
4. Phase 0 禁止伪 LIVE；缺数 → `quality=PARTIAL|DEGRADED`  
5. 不把 MCP 数据源失败传导为主链异常  

### 2.2 Principles

| # | 原则 |
|---|---|
| P1 | Shadow-first，主链 fail-open |
| P2 | Score + TTL + as_of；少裸 bool 当开关 |
| P3 | 周期阶段与单日微观**两层输出**（cycle + session） |
| P4 | 全球联动与 A 股微观解耦展示、再合成 |
| P5 | 托底小幅修正温度，绝不直接打回「乐观」 |
| P6 | Flags 机读稳定；话术进 narrative |
| P7 | 配置外置：`config/sentiment_shadow.yaml` |

---

## 3. 周期界定：全球科技回落周期

### 3.1 锚点与同步标的

| 角色 | 标的 | 用途 |
|---|---|---|
| t0 事件 | META 卖硬件 / 相关见顶日 | 周期计时；需数据定位，不写死 |
| 美股半导体 | SOXX / SMH | 全球风向 |
| 韩国半导体 | KRX 半导体 / 三星等代理 | 存储与去杠杆传导 |
| A股半导体 | 申万半导体 / 512480 等 | 本土科技情绪 |
| A股指数锚 | 上证；默认 **3750** | 系统性地板观测 |
| 宽基阶梯 | 沪深300 / 创业板指 / 中证1000 + 对应 ETF | 流动性传导 |

### 3.2 周期计量

- `drawdown_from_peak_*`：自周期峰值的回撤（优先用数据源区间字段）  
- `sync_corr` / `dispersion_global`：美/韩/A 回撤同步与分化  
- `a_vs_global_rel`：A 股半导体相对全球强弱（超跌 / 同步 / 抗跌）  
- 用户口述「回撤 30%+」= **量级锚**，实现以实拉校准，禁止硬编码为触发条件

---

## 4. 周期阶段模型（S0–S4）

融合危机事件位置 + 广度 + 杠杆微观：

| 阶段 | 名称 | 价格 | 广度 | 杠杆/情绪 | 主导观察 |
|---|---|---|---|---|---|
| **S0** | 高位见顶 | 滞涨、龙头松动 | 广度尚可但背离 | 杠杆/拥挤高位 | 全球分化初现、融资余额峰值 |
| **S1** | 下行初期 | 快速杀跌 | 广度先于指数恶化 | 跌停激增、恐慌 | AD、跌停占比、截面相关 |
| **S2** | 下行尾声/摸底 | 跌幅趋缓、深V | 开板率升、新低占比回落 | **融资重仓开板**、ETF 托 | 开板率、Top100 跌停、ETF Z |
| **S3** | 底部磨底 | 缩量震荡、无主线 | 弱修复、轮动乱 | 冰点、分歧、等催化 | 换手分位、涨跌停比、无主线时长 |
| **S4** | 上行初期 | 放量+主线 | 行业共振 | 情绪修复 | 共振、RSI 拐头、主线持续 |

**硬区分（来自 07-19 类比 + crisis 方法论）：**  
S2/S3 的反弹默认标为 **`rebound_type=TACTICAL`（交易性，2–4 周脉冲可能）**；  
仅当产业/政策/业绩**新驱动** + 共振确认，才允许 `rebound_type=CYCLICAL` 并点亮 S4 高置信。

### 4.1 与 V1.1 `rescue_sequence_stage` 对齐

| rescue_sequence_stage | 通常伴随周期 |
|---|---|
| SELLDOWN | S1 |
| PROTECT_INDEX | S1 末 / S2 初（托而不举） |
| STABILIZE_BROAD | S2（300→创→1000 传导中） |
| ROTATE_QUALITY | S3 末 / S4 初 |
| RELEASE_ELASTIC | S4 且共振确认后 |

二者**同时输出**，不互相覆盖。

### 4.2 `intervention_mode`（07-17 关键）

| 值 | 含义 |
|---|---|
| `NONE` | 无明确托底痕迹 |
| `DRAG_NOT_LIFT` | 托住流动性、不大拉、允许再阴跌换筹 |
| `LIFT_ATTEMPT` | 出现更积极的拉升痕迹（仍需看是否共振） |
| `UNKNOWN` | 数据不足 |

---

## 5. 五维因子体系 + 危机微观层

在 V2 叙述的「五大维度」上，保留 V1.1 的 L0–L6 工程字段，并**升格** 07-17~21 微观指标。

### 维度 G — 全球科技联动（Global）

| 因子 | 说明 |
|---|---|
| `global_semi_drawdowns` | SOXX / KR / A 自峰值回撤 |
| `global_sync_score` | 同步回落强度 0~1 |
| `a_vs_global_rel` | A 相对强弱 |
| `kr_semi_ret` / `kr_a_divergence` | 韩映射与背离 |
| `us_mega_tech_path` | 昨夜美股科技路径（含 RALLY_FADE） |
| `external_vol_pressure` | 外盘波动是否仍压制内部科技 |

### 维度 L — 流动性 / 去杠杆 / 托底（Liquidity & Leverage）— **本周补强核心**

| 因子 | 说明 | 07-17~21 锚 |
|---|---|---|
| `margin_top100_limit_down_n` | 融资净买/余额 Top100 中跌停只数 | 18（07-17） |
| `margin_top100_open_board_n` | 上述跌停中开板只数 | 20/21（07-21） |
| `margin_top100_new_limit_down_n` | 新增跌停 | 3（07-21） |
| `deleveraging_stress_index` | 0~1 综合：跌停占比、开板率、外溢 |
| `deleveraging_release_rate` | 开板率 = open/(open+still_down) | 压力释放 |
| `forced_selling_proxy` | 两融/拥挤赎回代理 | |
| `gjd_proxy_score` | 宽基 ETF 异常放量 Z | 托底痕迹 |
| `etf_premium_proxy` | 相关 ETF 溢价/折价 | 辅助 |
| `star_chinext_etf_bid` | 科创/创业 ETF 托举 | 07-21 尾盘 |
| `csi1000_liquidity_injection` | 小盘宽基是否出现注流动性迹象 | 07-21 要求 |
| `intervention_mode` | DRAG_NOT_LIFT 等 | 07-17 |
| `growth_etf_flow` / `liquidity_pulse` | 中期流与央行背景 | 低权重 |

**列表维护：** `data/a_share_margin_top100.csv`（大跌期日更；来源 ranking/margintrade）。

### 维度 B — 广度与传导（Breadth & Transmission）

| 因子 | 说明 |
|---|---|
| `advance_decline_tech` | 科技涨跌家数 |
| `limit_stress` / 全市场跌停家数、封单 | 恐慌 |
| `cross_section_corr_tech` | 「怎么买怎么跌」 |
| `theme_dispersion` / `rotation_speed` | 结构轮动 |
| `intraday_path` / `gap_hold_score` | 高开泄气等 |
| `transmission_hs300` | 沪深300 分时抵抗/企稳分 | 07-17 |
| `transmission_chinext` | 创业板是否复制 300 | 07-20 |
| `transmission_csi1000` | 1000 是否复制 | 07-21 |
| `liquidity_transmission_score` | 0~1 阶梯完成度 |
| `liquidity_transmission_stage` | `HS300_ONLY` / `TO_CHINEXT` / `TO_CSI1000` / `FAILED` / `UNKNOWN` |

### 维度 S — 情绪温度（Sentiment）

| 因子 | 说明 |
|---|---|
| `sentiment_temperature` | 0~100 |
| `blood_chip_harvest` | 低开收割 |
| `ipo_catalyst_heat` | 长鑫等 |
| `hk_repair_relative` | 港股修复对照 |
| `sse_anchor_*` | 3750 距离与守住质量 |

### 维度 M — 动量 / 右侧确认（Momentum & Confirmation）

| 因子 | 说明 |
|---|---|
| `bounce_quality` | NONE / WEAK_DEAD_CAT / INDEX_LED / BROAD_CONFIRMED |
| `sector_index_resonance` | 带头板块与指数共振 0~1 | 右侧关键 |
| `earnings_surprise_breadth` / `earnings_gap_fade` | 业绩季 |
| `leader_persistence` | 主线是否持续（防脉冲当主升） |
| `overseas_vs_cn_compute_rel` | 短线海外 vs 国产性价比 |

---

## 6. 周期定位器（Cycle Locator）

### 6.1 输出

```text
cycle:
  t0_event: "META_HARDWARE_SHOCK"   # 可配置
  t0_date: date | null              # 数据定位
  peak_dates: {soxx, kr_semi, a_semi}
  drawdowns: {soxx, kr_semi, a_semi}
  stage: S0|S1|S2|S3|S4
  stage_confidence: 0~1
  rebound_type: NONE|TACTICAL|CYCLICAL|UNKNOWN
  stage_switch_signals: [str]
  days_since_t0: int | null
```

### 6.2 阶段匹配（规则可配置，示意）

```text
S1: AD恶化 + 跌停升 + corr高 + 回撤加速
S2: 回撤斜率放缓 OR 深V
    + deleveraging_release_rate 升
    + (gjd/ETF 托底 OR transmission 启动)
S3: 开板后新增跌停低 + 缩量 + 高 dispersion + 无共振
    + 可选：距托住日进入「无主线」计时
S4: bounce_quality=BROAD_CONFIRMED
    + sector_index_resonance 高
    + volume_thrust 强
    + rebound_type 允许升为 CYCLICAL 的驱动标志
```

**07-21 校准样例（fixture 目标）：**  
`stage≈S2`（偏尾）或 `S2→S3`，`rebound_type=TACTICAL`，  
`intervention_mode=DRAG_NOT_LIFT`，  
`liquidity_transmission_stage=TO_CSI1000`，  
`deleveraging_release_rate` 高，  
flags 含 `BOUNCE_NOT_REVERSAL`、`DRAG_NOT_LIFT`、`LATE_BROAD_ETF_BID`、`WAIT_BROAD_BETA`。

### 6.3 ARS（Aggregate Regime Score，可选综合分）

五维子分加权 → 0~100 的 `ars`（与 temperature 可并存：temperature 更偏短窗情绪，ars 更偏周期位置）。  
权重进 yaml；缺失维则降权并降 confidence。

---

## 7. Snapshot 契约（V2.0）

```text
SentimentShadowSnapshot
  schema_version: "shadow-sentiment-2.0"
  market_scope: "CN"
  as_of, session, quality, confidence, ttl_seconds, source
  inputs_used[], missing_inputs[]

  cycle: { t0_event, t0_date, drawdowns, stage, stage_confidence,
           rebound_type, stage_switch_signals, days_since_t0 }

  # L0 指数锚 + 体制
  sse_anchor_px                    # default 3750
  sse_anchor_distance_bp
  sse_anchor_state                 # ABOVE_BUFFER|IN_ZONE|BREAK_TEST|UNKNOWN
  anchor_hold_quality
  market_regime_label              # CROWDED_UNWIND|INDEX_BID_TECH_PANIC|...
  cross_section_corr_tech
  crowding_unwind_score

  # 微观去杠杆（危机层）
  margin_top100_limit_down_n
  margin_top100_open_board_n
  margin_top100_new_limit_down_n
  deleveraging_stress_index
  deleveraging_release_rate
  forced_selling_proxy

  # 托底与传导
  intervention_mode
  gjd_proxy_score
  etf_premium_proxy
  star_chinext_etf_bid
  csi1000_liquidity_injection
  transmission_hs300
  transmission_chinext
  transmission_csi1000
  liquidity_transmission_score
  liquidity_transmission_stage

  # L1 路径/温度原材料
  volume_thrust
  advance_decline_tech
  limit_stress
  limit_down_count
  intraday_path
  gap_open_ret
  gap_hold_score

  # L2 结构
  theme_dispersion
  rotation_speed
  ipo_catalyst_heat
  equal_weight_tech_vs_cap
  beta_first_preference

  # L3 外盘
  kr_semi_ret
  us_mega_tech_path
  kr_a_beta
  kr_a_divergence
  overseas_vs_cn_compute_rel
  global_semi_drawdowns
  global_sync_score
  a_vs_global_rel

  # L4 事件
  earnings_surprise_breadth
  earnings_gap_fade
  blood_chip_harvest
  geopolitics_fear_proxy

  # L5 资金背景
  growth_etf_flow
  liquidity_pulse
  hk_repair_relative

  # L6 综合
  sentiment_temperature
  structure_quality                  # POOR|MIXED|GOOD
  bounce_quality
  rescue_sequence_stage
  rescue_playbook_match              # APR2025_GAP_DOWN_TREND|GAP_UP_FADE_WEAK|...
  sector_index_resonance
  ars                                # optional
  corroboration_flags[]
  narrative_bullets[]
  watch_checklist[]
```

---

## 8. Flags（V2.0 增量，兼容 V1.1）

**新增/升格：**

| Flag | 含义 |
|---|---|
| `CYCLE_S0`…`CYCLE_S4` | 当前阶段（可只打当前一个） |
| `REBOUND_TACTICAL` | 交易性反弹 |
| `REBOUND_CYCLICAL_CANDIDATE` | 中周期候选（严） |
| `DRAG_NOT_LIFT` | 托而不举 |
| `MARGIN_TOP100_STRESS` | 融资重仓跌停压力高 |
| `MARGIN_PRESSURE_RELEASE` | 开板率显著上升 |
| `TRANSMIT_HS300` / `TRANSMIT_CHINEXT` / `TRANSMIT_CSI1000` | 传导阶梯 |
| `CSI1000_NEED_LIQUIDITY` | 小盘仍需注流动性 |
| `RIGHT_SIDE_WAIT` | 右侧未确认、左侧不加仓叙事 |
| `NO_MAINLINE` | 托住后无主线磨底 |

**保留 V1.1：**  
`SSE_ANCHOR_*`、`CROWDED_UNWIND`、`GAP_UP_FADE`、`LATE_BROAD_ETF_BID`、`INDEX_BID_TECH_PANIC`、`KR_*`、`US_RALLY_FADE`、`BOUNCE_NOT_REVERSAL`、`WAIT_BROAD_BETA`、`GJD_BID_HINT`、`IPO_CATALYST_CX`、`HK_REPAIR_AHEAD` 等。

---

## 9. 飞书观测卡（V2.0 模板）

```text
Title: 🔭 Macro OS 观测 | CN Tech Cycle | {session} | {as_of}

**周期** t0={t0_date|TBD} | 阶段 {stage} ({stage_confidence:.0%}) | 反弹性质 {rebound_type}
**全球回撤** SOXX {dd%} / KR {dd%} / A半 {dd%} | A相对 {a_vs_global_rel}
**锚点** 上证 vs {sse_anchor_px} ({bp} bp) | {sse_anchor_state}
**体制** {market_regime_label} | 干预 {intervention_mode}
**温度** {temp}/100 | 结构 {structure_quality} | ARS {ars?}
**去杠杆** Top100跌停 {n} | 开板 {n} | 新增 {n} | 释放率 {rate:.0%}
**传导** {liquidity_transmission_stage} | 救援阶梯 {rescue_sequence_stage}
**路径** {intraday_path} | 反弹质量 {bounce_quality} | 共振 {resonance:.2f}

**Flags:** `...`

**读法**
- （弱修复期必含：反弹≠反转；托而不举≠V反）
- …

**观察清单（不构成交易指令）**
1. 融资 Top100 开板是否持续、新增跌停是否保持低位
2. 宽基 ETF 托底是连续行为还是一日尾盘
3. 传导是否稳定在 1000，而非仅 300
4. 有无带头板块与指数共振（右侧）
5. 外盘波动与科技财报/CapEx 是否仍压制

**边界:** Shadow only · 不修改 Kernel · 不改变 risk_budget
**Source/Quality/Confidence:** ...
```

---

## 10. 架构与模块

```
旁路:
  providers → SentimentShadowEngine
           → cycle locator + session microstructure
           → Snapshot → vault/shadow/*.jsonl
           → Feishu observation card

core/sentiment/
  types.py models.py engine.py
  cycle.py          # S0–S4 + rebound_type
  microstructure.py # margin top100, transmission
  aggregators.py flags.py stages.py

adapters/sentiment/
  protocol.py mock_provider.py manual_json_provider.py
  (phase2) live facades calling neodata/westock/tdx via subprocess/MCP

config/sentiment_shadow.yaml
data/a_share_margin_top100.csv
scripts/run_sentiment_shadow.py          # phase0 CLI
scripts/global_tech_cycle.py             # optional split
scripts/a_share_sentiment_daily.py       # phase1+ daily
```

**数据源优先级（Phase 2+，不绑死实现）：**  
1) neodata 全球 2) westock 融资/板块/资金 3) westock-tool 排行 4) tdx 扫描 5) akshare/web 兜底（标非实时）。  
Phase 0 仅 mock/manual fixture（含 07-17~21 场景）。

---

## 11. 与 Macro OS 主链衔接

| 模块 | 方式 |
|---|---|
| Kernel / risk_budget | **不读** shadow |
| Orchestrator | Phase1 决策卡后 try/except 旁路 |
| CIO | 可选 narrative 字符串，默认关 |
| Vault | shadow jsonl；可选审计 event 类型，回放忽略 |
| 未来 daily_macro 套件 | 可追加 stage 维度，仍非否决权 |
| Trinity | 不接收宏观否决；个股纪律不进本层 |

---

## 12. 分阶段交付

| Phase | 交付 | 成功标准 |
|---|---|---|
| **0** | 契约 + mock/manual + CLI + jsonl + 飞书 MD；**07-17~21 fixture** | 阶段/开板/传导/托而不举打标正确；主链零改 |
| **1** | orchestrator 旁路；配置锚 3750；去重发送 | 失败不影响 decision |
| **2** | LIVE_PARTIAL：指数锚、ETF、涨跌停、融资 Top100、主题、全球回撤代理 | quality 升级 |
| **3** | 历史样本标定阈值；描述统计 | 若动 Gate/Veto → **新 RFC** |

---

## 13. 测试矩阵（最低）

1. Fixture 07-17：高 `margin_top100_limit_down_n`，`DRAG_NOT_LIFT` 候选，stage S1/S2  
2. Fixture 07-21：高开板率，`MARGIN_PRESSURE_RELEASE`，transmission 到 CSI1000，`BOUNCE_NOT_REVERSAL`  
3. 锚点 3750 distance 计算  
4. 缺融资数据 → PARTIAL，不编造开板数  
5. `decision_kernel` 不得 import `core.sentiment`  
6. 飞书 footer 含 Shadow only  
7. S4 在无共振时不可高置信触发  

---

## 14. 配置要点（`config/sentiment_shadow.yaml`）

```yaml
version: "2.0"
enabled: true
market_scope: CN
sse_anchor_px: 3750
sse_anchor_buffer_pct: 0.008
cycle:
  t0_event: META_HARDWARE_SHOCK
  t0_date: null          # 填实值或启动时解析
stage_thresholds: { ... }
margin_top100_path: data/a_share_margin_top100.csv
transmission:
  enable: true
rescue_ladder_labels: true
emit_feishu: true
attach_to_cio: true   # review: Phase1 建议开周期摘要；Phase0 CLI 不依赖
# 人类纪律文案（不进交易 API）
playbook_hints:
  hs300_offset: -0.005
  chinext_offset: -0.01
  csi1000_offset: -0.02
```

---

## 15. Review Checklist

- [ ] 同意 V2.0 以**周期定位器**为第一性，而非单日触发器  
- [ ] 同意融资 Top100 跌停/开板为危机期一等指标  
- [ ] 同意 `intervention_mode=DRAG_NOT_LIFT` 与 `rebound_type` 强制输出  
- [ ] 同意 300→创→1000 传导状态机  
- [ ] 同意默认锚 3750 可配置  
- [ ] 同意仍不改 Kernel；-2% 挂单仅 checklist 文案  
- [ ] Phase 0 独立 CLI + 07-17~21 fixture  

## 16. Decisions to Approve

1. 采纳本文为 A 股情绪影子层 **SSOT V2.0**  
2. 07-17~21 摘要作为黄金校准样本  
3. Hard Veto 豁免 / 自动交易 **继续冻结**  
4. Phase 0 通过后即可开工 mock 引擎与飞书卡  

---

## Appendix A — 07-17~21 → 字段速查

| 叙事 | 字段/Flag |
|---|---|
| 爆杠杆、T+1 机构二次冲击 | `forced_selling_proxy`、`deleveraging_stress_index`、checklist 提周一窗口 |
| Top100 18 跌停 | `margin_top100_limit_down_n`、`MARGIN_TOP100_STRESS` |
| 托而不举 | `intervention_mode=DRAG_NOT_LIFT`、`DRAG_NOT_LIFT` |
| 2 个月无主线类比 | `NO_MAINLINE`、S3 时长、`rebound_type=TACTICAL` |
| 右侧等待 | `RIGHT_SIDE_WAIT`、`bounce_quality` |
| 300→创→1000 | `liquidity_transmission_stage` |
| 1000 -2% 剧本 | checklist 文案 + `TRANSMIT_CSI1000` |
| 20/21 开板 | `MARGIN_PRESSURE_RELEASE`、释放率 |
| 3750 | `sse_anchor_px` |
| 高开泄气+尾盘 ETF | `GAP_UP_FADE`、`LATE_BROAD_ETF_BID` |

## Appendix B — 边界：人类纪律 vs 系统

| 项目 | 系统 | 人类 |
|---|---|---|
| 阶段/释放率/传导 | ✅ 计算展示 | 决策参考 |
| 宽基分档挂单 | ❌ 不自动下单 | ✅ 自有执行 |
| 个股 T / 阿里仓位 | ❌ | ✅ |
| Kernel 预算 | ❌ 不读本层 | — |

---

## 17. Architecture Review Acceptance (2026-07-21)

**结论：方案通过（约 9.5/10），进入 Phase 0。**

### 17.1 已采纳的增强（相对正文初稿）

| 增强 | 落地 |
|---|---|
| S2/S3 边界模糊 | 允许 `stage` 主标签 + `stage_runner_up`；边界时 `stage_confidence` 压低；可选 `S2_S3_TRANSITION` 仅作展示标签（仍映射主 stage 为 S2 或 S3 之一便于枚举稳定） |
| `transmission_lag_days` | Snapshot 增加该字段；传导越快，S4 候选置信辅助加分（不单独升级 stage） |
| 飞书 **Δ 上一日** | Renderer 若提供 `previous` snapshot，输出 stage/开板/释放率/新增跌停 diff 行 |
| CIO narrative | Phase 1 默认 **打开** 周期定位摘要（`attach_to_cio: true` 建议）；Phase 0 CLI 仍独立 |
| margin_top100 维护 | S1/S2 **日更**；S0/S3/S4 **周更或按需**；CSV/`as_of` 必填，过期 → PARTIAL 并 flag `MARGIN_LIST_STALE` |
| Phase 0 成功标准 | **07-17~21 fixture 打标正确** 为硬门槛 |

### 17.2 冻结项（评审确认）

- V2.0 为 SSOT；07-17~21 黄金样本  
- Hard Veto 豁免冻结；Kernel 不读 shadow  
- `DRAG_NOT_LIFT` / `rebound_type` / 3750 可配置锚  

### 17.3 Phase 0 交付清单

1. `core/sentiment/*` 纯逻辑 + mock 输入  
2. `config/sentiment_shadow.yaml`  
3. `scripts/run_sentiment_shadow.py` CLI  
4. 飞书 markdown 渲染（含 Shadow only + 可选 Δ）  
5. `tests/test_sentiment_shadow_phase0.py`（07-17 / 07-21 / 锚点 / 缺数 / kernel 不 import）  
6. fixture JSON 于 `tests/fixtures/sentiment/`  

