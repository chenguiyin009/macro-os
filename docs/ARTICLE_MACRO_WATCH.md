---
tags: [macro-os,旁证, article-macro-watch, fallback, observational]
---

# 文章思路观测旁证 · 正式方案（含 FRED 中断回退）

## 目的

把作者叙事线（Fed 1y 加息定价 / WTI / 10Y / T10YIE / MOVE / 港股资金面一行）收成只读观测 artifact，挂到晨报/晚报 `②-c`（或等价「思路观测旁证」小节）。

本旁证是 **观察 + 报警**，不是控制层：

1. **永不**进入 `compute_combined_budget` / 四腿合成 min
2. **不改** `denom_policy` / `tight→0.35`
3. **不做**第六腿
4. 失败时允许 degraded / abstain，**禁止编造 bp 或价位**

实现入口：[`scripts/article_macro_watch_daily.py`](../scripts/article_macro_watch_daily.py)  
挂载入口：[`scripts/daily_macro_consolidated.py`](../scripts/daily_macro_consolidated.py) → `ensure_article_watch` / 报告段 `②-c`

与研究层缺字段规范 [`MACRO_DATA_FALLBACK`](MACRO_DATA_FALLBACK.md) 正交：后者服务周报/FeatureSchema；本文件只约束 **旁证日频链路**。

## 边界（硬）

| 可以做 | 不可以做 |
|---|---|
| 换数据源、换锚点、写 quality/degraded_reason | 改 C / four-leg min / Pine |
| 在旁证 JSON/MD 展示 TV 对照与 MOVE | 用旁证数值放松分母或抬高合成上限 |
| FRED 维护时走正式回退链 | 在回退链上「拍脑袋」填 bp |

## 正式回退优先级（生产默认）

晨报/晚报 consolidator **默认自动跑**本链；**不需要**人工每次传 `--tv-price`。  
`--tv-price` / `ARTICLE_TV_PRICE` / consolidator `--article-tv-price` 仅作 MCP 精报价可选注入（优先于 scanner）。

### 1. Fed 1y 加息定价 bp（`cme_settlement_cum_v1`）

| 顺序 | 路径 | 何时启用 |
|---|---|---|
| A | `cme-fedwatch.get_probabilities`（CME 结算 + FRED EFFR/target） | FRED 正常 |
| B | CME 结算 + **NY Fed EFFR API**（含 `targetRateFrom/To`）自算同算法 | A 因 FRED EFFR/target 失败 |
| C | CME 结算 + **ZQ=F / ^IRX** 估 EFFR → `current_target_range` | B 失败 |
| D | 已有 `current_mid` + **TV ZQU** 隐含：`tv_1y_hike_bp=(100−price−mid)×100` 升为观测主值（`algo=tv_zqu_implied_v1`） | A–C 结算/会议分布仍失败但 mid+价可得 |
| — | `fed_1y_hike_bp=null` + degraded | 以上皆无 → **不编造** |

锚点会议：`as_of` 起 365 日内最远一场 FOMC；期望利率为概率分布区间中点加权。

### 2. TV 对照列（`CBOT:ZQU2027`，不进 min）

| 顺序 | 路径 |
|---|---|
| 1 | `--tv-price` / 环境 `ARTICLE_TV_PRICE` / consolidator `--article-tv-price`（MCP 手递） |
| 2 | `scanner.tradingview.com/futures/scan` HTTP close |
| 3 | status=`unavailable`/`rate_limited`，数值 null；**不阻断**主 Fed 旁证 |

公式：`implied=100−price`；`tv_1y_hike_bp=(implied−current_mid)×100`；`delta=tv−CME自算`。

### 3. WTI

`CL=F` yfinance → `bar_sources`（TV/Stooq）→ FRED `DCOILWTICO`。叙事线绝对价 90（观察文案）。

### 4. 10Y

FRED `DGS10` → yfinance `^TNX`（FRED 维护时正式启用）。

### 5. T10YIE / 10Y 盈亏平衡

| 顺序 | 路径 | 滞后说明 |
|---|---|---|
| 1 | FRED `T10YIE` | 首选 |
| 2 | **Treasury.gov** 日频：名义 10Y − 实际 10Y（≈ BEI 观测代理） | 与 FRED T10YIE 可差数 bp；标注 source |
| 3 | 本地 `data/_bei_daily.csv` | **可能滞后约 1 个交易日**；FRED 恢复前的 last-good |
| — | null + degraded | 三者皆无 |

均值 lookback：约 1095 日历日（~3y），用于「相对近年均值偏离」观察文案。

### 6. MOVE（仅展示）

| 顺序 | 路径 |
|---|---|
| 1 | TradingView CFD scanner `TVC:MOVE` |
| 2 | abstain（与冲击吸收计算口径一致：冲击吸收脚本仍不把 MOVE 编入公式） |

旁证展示 MOVE **不进**合成 min，也 **不进**冲击吸收状态机计算。

### 7. 港股资金面旁证一行（不进 min）

一行展示：**南向净买卖** / **港股成交额（代理）** / **恒生科技 vs 恒指日涨跌**。

| 字段 | 优先级 / 来源 | 回退 |
|---|---|---|
| 南向净买卖 | Eastmoney `RPT_MUTUAL_DEAL_HISTORY`，`MUTUAL_TYPE=006`（南向合计），单位万元 | 失败 → null，文案写「不可用」；**禁止编造** |
| 港股成交额 | `2800.HK` Close×Volume 作流动性代理（`^HSI` Volume 常为 0） | null + 标注 proxy；非官方全市场总成交 |
| 恒科 vs 恒指 | 恒指 `^HSI`；恒科 `3032.HK`（HSTECH ETF；`^HSTECH` 常不可用）日涨跌差 | 任一腿失败 → 该段 null |

挂载：旁证 MD「港股资金面旁证」小节 + consolidator `②-c` 一行。仍**不进**四腿合成 min。

### 8. 油价文案（WTI ≥ 叙事线）

过线时文案必须写 **全球风险溢价**；**禁止**暗示「中国特有崩盘 / 单独归因 A 股」。  
门槛绝对价仍为 90（观察文案，未标定）。

## 晨晚例行（consolidator）

```bash
SECTOR_PROXY=off python scripts/daily_macro_consolidated.py --report-dir ./output
# 可选 MCP 精报价（不传则自动 scanner）：
SECTOR_PROXY=off ARTICLE_TV_PRICE=95.315 python scripts/daily_macro_consolidated.py ...
# 或：
... --article-tv-price 95.315 --article-tv-symbol CBOT:ZQU2027
```

- 子进程继承环境；`ARTICLE_TV_*` 无需改子脚本。
- 产物：`output/article_macro_watch_<as_of>.{md,json}`；主报告 `②-c` 挂载。
- JSON 含 `fallback_policy` 字段，标明本方案版本与各腿优先级（便于审计）。

## 质量与报警

- `quality=ok|partial|degraded`；MOVE 单独 abstain **不**单独把整份标 degraded。
- Fed 门槛文案（观察）：70 / 100 / 110 bp；WTI 叙事线 90（过线=全球风险溢价，禁止中国特有崩盘暗示）。
- 与官网 QuikStrike / 盘中 FedWatch 允许偏差（日结算 vs 盘中、日程硬编码、月末敏感）。

## 版本

- 方案名：`article_macro_watch_fallback_v1`
- 算法：`cme_settlement_cum_v1`（主）/ `tv_zqu_implied_v1`（仅 D 路径）
- 引入：2026-10-22（FRED 维护事件后正式化）

