# 科技板块轮动 v2.6.1r · 无头（headless）复刻 — grokbot 交接说明

> 本文件说明如何在不依赖 TradingView 桌面端 / Chrome CDP / 人工挂载 Pine 的前提下，用纯 Python 复刻
> 「科技板块资金轮动观测机 v2.6.1r」的每日读数，供 grokbot 在云主机上定时运行。

---

## 0. 为什么需要 headless 复刻（关键背景）

原始的「科技板块轮动 TV 快照」任务**无法在云主机/沙箱里直接运行**，因为它依赖：

1. 本机 TradingView Desktop（Chrome + `--remote-debugging-port=9222` CDP）；
2. 人工在 QQQ 1D 图上**手动挂载** Pine 指标 v2.6.1r（沙箱无法自动粘贴/应用 Pine 源码）；
3. 通过 MCP `data_get_pine_tables` / `data_get_study_values` / `capture_screenshot` 读取指标值与截图。

因此，把「TV 快照」原样交给 grokbot 是不可能的。**替代方案**：把 v2.6.1r 的打分数学 1:1 移植到 Python（yfinance 拉价），
产出与 TV 快照同口径的 13 主题仪表盘 + 归因面板 + 所选主题轮动分数。脚本即 `scripts/tech_rotation_daily.py`。

事实源（SoT）：`pine/pine_v26_1r.pine`（位于根仓 `tradingview/pine/`，本脚本是其移植，不运行时读取该文件）。

---

## 1. 运行方式

```bash
# 默认：以最新交易日为 as_of（自动判定），输出到 output/tech_rotation_<date>.json + .md
python scripts/tech_rotation_daily.py

# 指定 as_of（复刻某个历史快照时必填，例如复刻 TV 09:29 盘前快照 = 前一美交易日收盘）
python scripts/tech_rotation_daily.py --date 2026-08-31

# 强制刷新行情（忽略本地缓存）
python scripts/tech_rotation_daily.py --date 2026-08-31 --force-refresh

# 指定输出目录（默认 ../output 即 tradingview/output）
python scripts/tech_rotation_daily.py --report-dir /path/to/out
```

- 依赖：`pandas`、`numpy`、`yfinance`（均纯 Python，云主机 `pip install` 即可）。
- 无需任何本地代理即可运行；见下方「代理」一节。

---

## 2. 代理（云主机无本地代理时）

脚本默认 `DEFAULT_PROXY="http://127.0.0.1:7890"`（本机 Clash 出口）。在 grokbot 云主机上**没有该代理**，
按以下任一方式关闭即可走直连：

- 环境变量 `SECTOR_PROXY=off`（或 `none`/`0`/`false`/`no`/`""` 均可）；
- 或显式设置系统代理 `HTTPS_PROXY=` / `HTTP_PROXY=`（脚本优先采用这两个环境变量）。

`_ensure_proxy()` 的优先级：`HTTPS_PROXY`/`HTTP_PROXY` 环境变量 > `SECTOR_PROXY` 开关 > 默认 `127.0.0.1:7890`。

---

## 3. 输出

- `output/tech_rotation_<as_of>.json` — 机器可读：13 主题（含 rel5/rel20/rel60/abs5/trend/vol_ratio/breadth/score/state/reason）、
  10 项归因信号、整体状态、所选主题轮动分数、数据来源（实时/缓存）。
- `output/tech_rotation_<as_of>.md` — 人读报告：一、主题仪表盘 / 二、归因面板 Top6 / 三、所选主题轮动分数，附教学免责声明。

所选主题默认为 **半导体（SMH）**，对应 TV 指标 `in_9`，其分数即 TV 快照里的「Selected Rotation Score」。

---

## 4. 与 TV 快照的口径校验（已验证）

以 TV 快照 `rotation_results/rotation_result_2026-09-01_0929.md`（盘前 09:29 GMT+8 = 美 08-31 收盘）为基准，
`--date 2026-08-31` 复刻结果 **13 主题中 12 个分数完全一致**，整体均分 62.3 vs TV 61.5。

| 主题 | 代理 | headless 复刻 | TV 快照 | 一致 |
|---|---|---:|---:|:--:|
| 半导体 | SMH | 44 | 44 | ✅ |
| 软件 | IGV | 89 | 89 | ✅ |
| 云计算 | SKYY | 99 | 99 | ✅ |
| 网络安全 | CIBR | 97 | 97 | ✅ |
| 机器人 | BOTZ | 34 | 34 | ✅ |
| AI综合 | AIQ | 93 | 93 | ✅ |
| AI应用软件 | 篮子 | 80 | 80 | ✅ |
| 光通信零部件 | 篮子 | 52 | 52 | ✅ |
| AI网络 | 篮子 | 44 | 44 | ✅ |
| 储存 | 篮子 | 92 | 92 | ✅ |
| 数据中心电力散热 | 篮子 | 16 | 16 | ✅ |
| AI硬件卖方 | 篮子 | 34 | 34 | ✅ |
| 云巨头/AI买方 | 篮子 | 36 | 26 | ⚠️ +10 |

**唯一残余差异：云巨头/AI买方 36 vs 26（+10）**。拆解后确认是单一 `vol_score` 单元——本脚本算得该 4 股篮子
在 08-31 的 1 日相对收益（rel1）为微正（+10 vol 分），而 TV 的 Pine 判为 ≤0（+0 vol 分）。**两者对该主题的状态判定完全一致
（均为「资金撤出 / 跟涨假强」）**，属 4 股篮子 1 日相对收益符号的边界情形，不影响整体读数结论。详见 `docs/tech_rotation_validation_2026-08-31.md`。

---

## 5. 已知约束 / 上线前必读

- **有效期**：脚本内置 `EXPIRY = 2026-11-07`（对应 v2.6.1r 指标授权/逻辑冻结日）。到期后需重新校验或同步新版本 Pine 数学。
- **与真实 TV 快照的区别**：本脚本是**数学移植**，不是 TV 实时指标。TV 快照的文本/截图仍需本机人工挂载指标后由 09:20 自动化产出；
  本脚本供 grokbot 在云上产出**同口径数值**，二者应一致（除上表 ⚠️ 项）。
- **行情缓存**：`data/tech_price_cache/<code>.csv`，失败自动回退；`--force-refresh` 强制重拉。
- **免责**：仅供信息参考与教学演示，不构成投资建议。

---

## 6. grokbot 定时运行建议

```bash
# 示例：每个美股交易日盘后（美东 16:00 后，约 GMT+8 次日 04:00–06:00）跑一次
0 5 * * 1-5 cd /path/to/macro-os && python scripts/tech_rotation_daily.py
```
（具体时区按 grokbot 云主机设定；`as_of` 会自动取最新交易日，无需显式 `--date`。）
