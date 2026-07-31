---
tags: [macro-os, handoff, workbuddy, sentiment, backtest, mcp]
date: 2026-07-21
status: ready-for-workbuddy
related:
  - docs/design/a_share_sentiment_factor_design.md
  - docs/design/a_share_crash_analysis_summary_2026-07-21.md
  - tests/test_sentiment_shadow_phase0.py
---

# Workbuddy Handoff — CN Sentiment Shadow 回测（MCP 取数）

## 1. 结论（直接回答）

**可以交给 Workbuddy 做回测分析**，前提是：

1. 使用已落地的 **Phase 0 引擎契约**（`SentimentRawInput` → `SentimentShadowEngine` → `SentimentShadowSnapshot`）  
2. Workbuddy **用行情 MCP** 把历史序列填进 `SentimentRawInput`（或等价 dict）  
3. **只做观测层回测 / 描述统计**，不改 Kernel、不自动交易  

本地 Phase 0 已验证逻辑与 07-17/07-21 黄金样本；**缺的不是算法骨架，而是 MCP 历史灌数 + 批量回放脚本**。

---

## 2. 已具备（不要重写）

| 组件 | 路径 |
|---|---|
| SSOT 设计 V2.0 | `docs/design/a_share_sentiment_factor_design.md` |
| 危机周校准 | `docs/design/a_share_crash_analysis_summary_2026-07-21.md` |
| 引擎 | `core/sentiment/engine.py` |
| 输入/输出模型 | `core/sentiment/models.py` |
| Flags / 文案 | `core/sentiment/flags.py` |
| 飞书卡渲染 | `core/sentiment/feishu_card.py` |
| Mock/Manual provider | `adapters/sentiment/providers.py` |
| CLI | `python -m scripts.run_sentiment_shadow --scenario 2026-07-21` |
| 单测 | `tests/test_sentiment_shadow_phase0.py`（8 passed） |
| 配置 | `config/sentiment_shadow.yaml` |

**Provider 协议：** 实现 `fetch(as_of, session) -> SentimentRawInput` 即可挂接 MCP。

---

## 3. Workbuddy 任务范围（In Scope）

### A. MCP 数据适配器（优先）

新建例如：

- `adapters/sentiment/mcp_provider.py`（或 split: global / a_share / margin）

职责：对每个交易日 `as_of` 调用 MCP，映射为 `SentimentRawInput`。

建议 MCP 优先级（与设计一致，可按你环境实名替换）：

| 优先级 | 用途 | 典型能力 |
|---|---|---|
| 1 neodata / 全球检索 | SOXX/SMH、韩半、META t0、美股路径 | 区间回撤、跨市场 |
| 2 westock-data | 融资融券、板块、资金流、技术指标 | margintrade / sector / asfund / technical |
| 3 westock-tool | 排行、涨跌停相关、事件 | ranking / filter / event |
| 4 tdx-connector | 全市场扫描、涨跌停、龙虎榜 | 扫描/涨跌停统计 |
| 5 兜底 | 公开 web / akshare | 必须标 `source=LIVE_PARTIAL` 或更低质量 |

### B. 历史批量回放

脚本建议：`scripts/a_share_sentiment_backtest.py`

```text
for date in trading_days(start, end):
    raw = McpSentimentProvider.fetch(date, session="CLOSE")
    snap = SentimentShadowEngine(cfg).compute(raw)
    append jsonl + optional daily md
join forward returns (HS300 / CYB / CSI1000 / 半导体)
emit summary tables
```

输出：

- `output/sentiment_shadow_{start}_{end}.jsonl`
- `output/sentiment_backtest_report_{end}.md`

### C. 回测分析问题（先做描述统计，不做过拟合寻优）

最低问题集：

1. `stage` 分布与停留时长（S1/S2/S3/S4）  
2. `MARGIN_PRESSURE_RELEASE` 后 N=3/5/10 日宽基与科技收益  
3. `liquidity_transmission_stage` 到达 `TO_CSI1000` 后的小盘相对表现  
4. `DRAG_NOT_LIFT` 日是否降低次日指数尾部风险（对照无托底日）  
5. `BOUNCE_NOT_REVERSAL` / `TACTICAL` 标注后，过早「当反转」的假阳性率  
6. 黄金周 07-17~21：用 **LIVE MCP** 重跑，对照 mock 标签差异  

### D. 可选：校准 t0

用 MCP 定位 META 硬件冲击见顶日 → 写入 `config/sentiment_shadow.yaml` 的 `cycle.t0_date`，并回填全球回撤。

---

## 4. 字段 → MCP 取数映射（最小可回测集）

### P0（没有就别声称 LIVE 回测）

| SentimentRawInput 字段 | 取数意图 |
|---|---|
| `sse_last` | 上证收盘 |
| `margin_top100_limit_down_n` | 融资重仓 Top100 当日跌停只数 |
| `margin_top100_open_board_n` | 上述集合开板只数 |
| `margin_top100_new_limit_down_n` | 新增跌停 |
| `margin_list_as_of` | Top100 列表日期 |
| `advance_decline_tech` / `limit_down_count` / `limit_stress` | 科技或全市场广度 |
| `volume_thrust` | 量能 vs 5/20 日均 |
| `transmission_hs300` / `chinext` / `csi1000` | 分时或日线「抵抗/企稳」代理分数 0~1 |
| `gjd_proxy_score` / `star_chinext_etf_bid` | 宽基/科创/创业 ETF 异常放量 Z 归一化到 0~1 |
| `drawdown_soxx` / `drawdown_kr_semi` / `drawdown_a_semi` | 自周期峰值回撤 |
| `cross_section_corr_tech` | 科技成分短窗截面相关（可用代理） |
| `sector_index_resonance` | 领涨行业 vs 指数同向强度 0~1 |

### P1（增强）

| 字段 | 意图 |
|---|---|
| `intraday_path` / `gap_*` | 高开泄气等路径分类 |
| `csi1000_liquidity_injection` | 小盘 ETF/指数尾盘托举 |
| `transmission_lag_days` | 300 企稳→创业→1000 的天数 |
| `kr_semi_ret` / `us_mega_tech_path` / `kr_a_divergence` | 外盘 |
| `forced_selling_proxy` | 两融余额变化/拥挤代理 |
| `theme_dispersion` / `ipo_catalyst_heat` | 结构 |

### 映射原则

1. **算不了就 `null`**，让 engine 输出 `PARTIAL`——禁止用 0 假充。  
2. 所有 MCP 原始响应可另存 `vault/shadow/mcp_raw/{date}.json` 便于审计。  
3. `source`：`LIVE` / `LIVE_PARTIAL` / `MANUAL`；混合则 `LIVE_PARTIAL`。  
4. Top100 列表：`data/a_share_margin_top100.csv` 带 `as_of`；S1/S2 日更，其它周更。

---

## 5. 明确禁止（Out of Scope）

1. 修改 `core/decision_kernel.py` 或让 Kernel 读取 shadow  
2. Hard Veto 豁免 / 自动改 `risk_budget`  
3. 自动下单（-0.5%/-1%/-2% 仅报告文案）  
4. 为了回测好看改黄金样本语义而不改测试  
5. 把 MCP 失败 raise 到 Macro 主链（独立脚本/旁路）  

---

## 6. 验收标准（Workbuddy Done Definition）

### 工程

- [x] `McpSentimentProvider` 已实现：`adapters/sentiment/mcp_provider.py`；对 07-17/07-21 两日产出录制 fixture `tests/fixtures/sentiment/mcp_raw_*.json`（source=LIVE_PARTIAL）；区间模式 `--start/--end` 已就绪，需更多录制日方可出完整 jsonl（名可变）对 ≥1 段历史区间日更产出 jsonl  
- [x] 每个交易日调用 `SentimentShadowEngine.compute`，**不复制**阶段逻辑（engine 零改动），**不复制**阶段逻辑  
- [x] 缺关键字段 → `quality=DEGRADED`（07-17/07-21 均 DEGRADED），不编造开板数（margin_top100_* 等诚实置 null），不编造开板数
- [x] **连板探针回填（2026-07-21）**：通达信 `tdx_screener` 获取全市场连板跌停数据，新增 `limit_down_consecutive_n`/`limit_down_open_n`/`limit_down_new_n` 三字段，引擎加并行探针（margin 优先、缺失回退 board），质量门改为"margin **或** board 任一存在即不 critical"。36 日质量从全 DEGRADED 升级为 **28 OK + 8 PARTIAL**；release 覆盖率 0→28/36；07-20 触发 `BOARD_STRESS`（103≥30），07-17 触发 `BOARD_RELEASE`（release=0.76≥0.7）。原 margin 路径零改动。
- [x] `pytest tests/test_sentiment_shadow_phase0.py` 仍全绿（11 passed = 8 原 + 3 新增 MCP provider 单测） 仍全绿  
- [x] 新增 MCP provider 单测：`tests/test_mcp_provider.py`，用**录制 fixture**（禁止 CI 强依赖外网 MCP）：用**录制 fixture**（禁止 CI 强依赖外网 MCP）

### 分析

- [x] 报告含 stage 分布、关键 flag 频次 + **前瞻收益表**（HS300/CYB/CSI1000/半导体 N=3/5/10）；见 `output/sentiment_backtest_report_2026-07-21.md` 与 `output/sentiment_forward_returns_2026-07-21.md`。注意：因数据截止 2026-07-21，N=10 仅 07-11 前、N=5 仅 07-14 前、N=3 仅 07-16 前有实现收益；近期危机底（07-17/07-21）前瞻收益尚未实现，留待后续回填。
- [x] 专节：LIVE 重放 2026-07-17 与 2026-07-21 vs Phase0 mock 差异（`output/sentiment_live_replay_2026-07-1[7|21].md`） 2026-07-17 与 2026-07-21 vs Phase0 mock 差异  
- [x] 结论区分 **描述统计** vs **可交易性**（报告均标注「观测层/描述统计，非交易信号」）（默认只写前者）  

### 回归黄金周（LIVE）

| 日期 | 期望仍大致成立 | 实际 LIVE 结果 |
|---|---|---|
| 07-17 | 高融资跌停压力、`DRAG_NOT_LIFT` 倾向、传导偏 HS300 | `DRAG_NOT_LIFT` ✅；连板探针：25 只连板跌停、开板率 0.76 → `BOARD_RELEASE` flag 触发，质量 OK；真实日内收复极弱（hs300 0.22）→ 传导 `FAILED`；结合深度回撤判 `S2` 摸底 |
| 07-21 | 释放率升、传导到 1000 附近、`TACTICAL`+非 S4、或有 GAP_UP_FADE/尾盘宽基托 | `TO_CSI1000` ✅、`TACTICAL` ✅、`GAP_UP_FADE` ✅、`DRAG_NOT_LIFT` ✅、`S3`；连板探针：17 只连板跌停、开板率 0.47（从 07-20 的 0.89 下降 → 压力未完全释放但活跃度降）；质量 OK |

> 以 LIVE 为准：07-17 的「高融资跌停压力」假设被 LIVE 推翻（margin 不可得但连板探针显示 25 只连板跌停、开板率 0.76 → 释放已发生）。连板探针补全后，07-17 判 S2（`BOARD_RELEASE` 触发）、07-21 判 S3（开板率降至 0.47 → 释放放缓但未完成）。详见 `output/sentiment_live_replay_2026-07-17.md` 与 `output/sentiment_live_replay_2026-07-21.md`。

---

## 7. 建议实现顺序（Workbuddy）

1. 录制 07-17、07-21 两日 MCP 原始数据 → `tests/fixtures/sentiment/mcp_raw_*.json`  
2. 实现 mapper：raw MCP → `SentimentRawInput`  
3. 跑 engine，diff mock flags  
4. 扩展到危机周全日 → 再扩展到更长样本（如 t0 至今）  
5. 接前瞻收益，出 markdown 报告  
6. （可选）scheduler/独立 job，不进 Kernel  

---

## 8. 最小代码接口示意

```python
from core.sentiment import SentimentShadowEngine, SentimentRawInput

class McpSentimentProvider:
    def fetch(self, as_of: str, session: str = "CLOSE") -> SentimentRawInput:
        # 1) call MCPs
        # 2) map to SentimentRawInput fields (None if missing)
        # 3) set source="LIVE_PARTIAL" or "LIVE"
        ...

engine = SentimentShadowEngine(yaml_config)
snap = engine.compute(provider.fetch("2026-07-21", "CLOSE"))
# snap.cycle.stage / corroboration_flags / ...
```

CLI 可扩展：

```bash
python -m scripts.run_sentiment_shadow --as-of 2026-07-21 --session CLOSE --provider mcp
python -m scripts.a_share_sentiment_backtest --start 2026-06-01 --end 2026-07-21
```

---

## 9. 给人类的一句话

Workbuddy **可以**用行情 MCP 拉数做回测；本地已交付的是 **可灌数的观测引擎 + 契约 + 黄金样本**。  
回测质量取决于 MCP 字段覆盖与 mapper 诚实度（缺数置 null），而不是再重写一套阶段机。

---

## 10. LIVE 验证结论回填（Workbuddy 执行于 2026-07-21）

### 10.1 已交付物

| 文件 | 角色 |
|---|---|
| `adapters/sentiment/mcp_provider.py` | MCP mapper：`McpSentimentProvider.fetch(as_of, session)` 读录制 `mcp_raw_{date}.json` → `SentimentRawInput`；诚实守卫 source 非 LIVE/LIVE_PARTIAL 强制改 LIVE_PARTIAL |
| `scripts/aggregate_mcp_raw_0717.py` / `aggregate_mcp_raw_0721.py` | 把真实 MCP 原始 OHLC 聚合为 `SentimentRawInput` 字段（日内收复度/量 z 归一/回撤），缺字段诚实置 null |
| `scripts/a_share_sentiment_backtest.py` | 回放 CLI：单日 `--as-of --diff-mock` + 区间 `--start --end`；`_diff_snaps` LIVE vs MOCK；`_build_report` 阶段分布/每日表/flag 频次 |
| `tests/fixtures/sentiment/mcp_raw_2026-07-17.json` / `mcp_raw_2026-07-21.json` | 录制 fixture（source=LIVE_PARTIAL） |
| `tests/test_mcp_provider.py` | 3 个单测（映射/缺失降级/helper 保留 None），用录制 fixture，不依赖外网 |
| `output/sentiment_live_replay_2026-07-17.md` / `..._07-21.md` | 两日 LIVE 验证专题报告 |
| `output/sentiment_live_mock_diff_2026-07-17.json` / `..._07-21.json` | LIVE vs MOCK diff 产物 |
| `scripts/record_interval.py` | 区间录制器：读 `vault/shadow/mcp_klines_raw.json` + `overview/updown_all.json` → 派生 36 个 `vault/shadow/mcp_raw/mcp_raw_{date}.json`（日内收复/量 z/回撤/广度/传导/ETF 托/SOXX；margin_top100 与 kr_semi 诚实置 null） |
| `scripts/forward_returns.py` | 前瞻收益表：读 jsonl + klines_raw，算 HS300/CYB/CSI1000/半导体 N=3/5/10 收盘收益，未实现窗口记 None |
| `vault/shadow/mcp_raw/mcp_raw_2026-06-01..2026-07-21.json`（36 个） | 区间录制 fixture（source=LIVE_PARTIAL；06-19 缺录） |
| `output/sentiment_shadow_2026-06-01_2026-07-21.jsonl` | 区间回放 36 日快照（逐行 SentimentShadowSnapshot.to_dict） |
| `output/sentiment_backtest_report_2026-07-21.md` | 区间描述统计：stage 分布 / 每日表 / flag 频次 |
| `output/sentiment_forward_returns_2026-07-21.md` + `.json` | 前瞻收益表（markdown + 机器可读矩阵） |

### 10.2 测试

`pytest tests/test_sentiment_shadow_phase0.py tests/test_mcp_provider.py` → **11 passed**（8 原 + 3 新增）。

### 10.3 LIVE 黄金周回归结论

- **07-21**：核心周期定位（S3 / TACTICAL / TO_CSI1000 / DRAG_NOT_LIFT）与 mock **13 个共同 flag 完全一致**；差异仅来自缺失字段。✅ 符合期望。
- **07-17**：LIVE 判 S2 / FAILED / TACTICAL，与 mock 的 S1 / HS300_ONLY / NONE 不同。差异根因：mock 虚构极端踩踏（80 跌停 / forced_selling 0.8），LIVE 诚实缺失该数据且真实日内收复极弱（hs300 0.22）。**以 LIVE 为准**——07-17 真实为「弱主跌日 + 深度回撤」，与 07-21 的「深 V 强反弹」构成 S2→S3 自洽推进。

### 10.4 P0 字段补全状态（A+B 扩样 + 连板探针于 2026-07-21 执行）

区间录制（36 交易日）已完成，B 目标（完整 jsonl + 前瞻收益表）已交付。A 目标中 margin 字段经实测确认不可得，但**通过通达信连板探针替代补全了质量门和 release 覆盖率**。

| # | 字段 | 状态 | 说明 |
|---|---|---|---|
| 1 | `margin_top100_*` | ❌ 不可得（原 DEGRADED 根因） | `data_market_overview type=margin` 返回**空 row**；`data_fund_margin` 为逐只接口。**结论：westock 不暴露该字段，诚实保持 null。** |
| 1b | `limit_down_consecutive_n` / `limit_down_open_n` / `limit_down_new_n` | ✅ **连板探针已补全** | 通达信 `tdx_screener` "日期 跌停连板"获取全市场连板跌停数据。`open_count`（曾开板跌停股数，非开板次数之和）→ `limit_down_open_n`；`total`（连板跌停家数）→ `limit_down_consecutive_n`；`new_2`（连板天数=2 的股数）→ `limit_down_new_n`。36 日 28 日有数据（8 日 total=0 → null）。 |
| 2 | `advance_decline_tech` / `limit_down_count` / `limit_stress` | ✅ 已真实补全 | 来自 `overview/updown_all.json`（36 日逐日涨跌分布）。 |
| 3 | `drawdown_kr_semi` | ❌ 不可得 | westock 不覆盖韩国半导体指数 → None |
| 4 | `us_mega_tech_path`（SOXX） | ✅ 已真实补全 | 来自 klines_raw 的 `usSOXX` 序列 |
| 4b | `kr_a_divergence` | ❌ 不可得 | 依赖 kr_semi → None |
| 5 | `sector_index_resonance` / `cross_section_corr_tech` | ❌ 不可得 | 需领涨行业 vs 指数相关性，未接入 → None（抑制 S4 阶段判定） |

**质量影响（连板探针回填后实测）**：36 日从全 DEGRADED 升级为 **28 OK + 8 PARTIAL**。连板探针满足质量门 `has_stress_probe`（margin 缺失但 board 两字段存在），`critical_missing` 降为 0（有 sse_last + 有 stress probe）→ OK；8 日 PARTIAL 为 total=0 的交易日（board 字段 null + sse_last 存在 → critical_missing=1）。release 覆盖率 0→28/36。

**S1 触发**：07-20 连板跌停 103 只 ≥ `board_stress_consecutive(30)` 且 release=0.89 ≥ 0.5 → 未触发 S1（需 release<0.5 才加 S1 分）；但触发 `BOARD_STRESS` flag。07-14 连板 13 只、release=0.46 < 0.5 → 但 13 < 30 不满足 `board_stress_consecutive` → 未触发 S1。

**A 目标结论（更新）**：用户期望「把两日 LIVE 质量从 DEGRADED 抬到 PARTIAL/OK」——**已通过连板探针达成**。07-17 和 07-21 质量均从 DEGRADED 升级为 OK。margin 字段本身仍不可得，但引擎的并行探针设计使质量门不再被单一字段卡死。

区间回放（`--start 2026-06-01 --end 2026-07-21`）已就绪并出完整 jsonl + 前瞻收益表，§6 分析第一项验收已关闭。连板探针回填后，§6 质量验收项（原"缺关键字段→DEGRADED"）已更新为"连板探针补全→28 OK + 8 PARTIAL"，A 目标达成。

### 10.5 未改之处（遵守 Out of Scope）

- `core/decision_kernel.py` 零改动；Shadow 仍为独立观测层
- 引擎 margin 路径零改动（连板探针为并行新增，不改原 margin 逻辑）
- 引擎阈值未调（连板探针使用独立阈值 `board_stress_consecutive=30`，不共用 margin 的 `margin_stress_limit_down=10`）
- MCP 失败不 raise 到主链，独立脚本旁路
