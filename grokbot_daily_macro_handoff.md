# Grokbot 交接文档 · 每日宏观四腿统一报告

> 本文件供 **grokbot（或其它外部调度器）** 从 GitHub 拉取并运行 `daily_macro_consolidated.py` 使用。
> 人类阅读版说明见正文，末尾附「运行命令」与「汇报口径」可直接复制。

## 1. 这个任务是什么
每个美国交易日盘前，生成**每日宏观四腿统一报告**（观察性，非交易信号）：
1. **分母状态机 v1.6**（denominator）— 利率/美元/信用/久期维度
2. **科技减震器**（tech dampener）— SOXX 回撤 → 内核风险预算
3. **主题状态机 v3.1r**（theme）— 13 主题轮动叙事
4. **NQ 第四腿动因**（nq driver）— 纳指/日元/美元/比特币多空博弈
→ 合成四腿的**合成上限（combined_budget）**与**约束项（binding leg）**，输出 `output/daily_macro_<date>.{md,json}`。

`daily_macro_consolidated.py` 会自动以 subprocess 调用上面四个子脚本（并可选跑板块轮动/冲击吸收旁证），统一汇总。

## 2. 前置依赖（grokbot 环境必须满足）
| 依赖 | 说明 |
|---|---|
| Python 3.10+ | 建 venv 即可 |
| numpy / pandas / yfinance / akshare | `pip install numpy pandas yfinance akshare` |
| 网络 | FRED 直连（无需代理）；yfinance 需能访问 Yahoo 数据 |
| 工作目录 | 必须在仓库根（`macro-os/`）下运行；脚本靠 `parents[1]` 定位 `output/` 与 `config/` |

## 3. 代理开关（关键，避免卡死）
四腿子脚本之前硬编码 `127.0.0.1:7890`（作者本机代理）。已加 `SECTOR_PROXY` 开关（向后兼容）：
- `SECTOR_PROXY=off|none|0|false|no|""` → 完全不设代理（grokbot 云机用此项，依赖直连）
- `SECTOR_PROXY=http://host:port` → 用指定代理
- 已设 `HTTPS_PROXY`/`HTTP_PROXY` → 以环境既有代理为准
- 未设置 → 默认 `127.0.0.1:7890`（作者本机，行为不变）

> `daily_macro_consolidated.py` 会把 `SECTOR_PROXY` 经父环境透传给所有子进程，无需逐脚本设置。

## 4. 从 GitHub 下载并运行（完整流程）
```bash
# 统一分支 daily-macro-stable 已含四腿 + 板块轮动全部脚本（grokbot 用这一支即可覆盖两个报告）
git clone -b daily-macro-stable https://github.com/chenguiyin009/macro-os.git
cd macro-os

python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install numpy pandas yfinance akshare

# 推荐：省略 --date，让脚本按 NY 现金时段自动解析已收盘日
# （16:00 ET 前→上一 BD；收盘后→当日 BD；周末/假日→再往前）
# 也可显式传 YYYY-MM-DD；不要传中文「今天」。

# 运行（关掉本机代理默认值；--report-dir 指定输出落点）
SECTOR_PROXY=off unset HTTPS_PROXY HTTP_PROXY
SECTOR_PROXY=off python scripts/daily_macro_consolidated.py --report-dir ./output
# 或：SECTOR_PROXY=off python scripts/daily_macro_consolidated.py --date 2026-09-08 --report-dir ./output
```
- 失败只报错误、不要重试超过一次（子脚本 yfinance 已内置 3 次退避重试）。
- 退出码 `0` = 成功写观察文件；但若 JSON 中 `synthesis.non_actionable` / `incomplete_bar` 为 true，**不可当执行信号**。
- 退出码 `2` = DATE 非法（含中文日期）或其它硬失败。

## 5. 输出文件
- `output/daily_macro_<date>.md` + `.json`（主报告，含四腿明细与合成上限）
- 子产物：`denominator_state_<date>.{md,json}` / `tech_drawdown_<date>.json` / `theme_state_machine_<date>.{md,json}` / `nq_driver_<date>.{md,json}`

## 6. 汇报口径（grokbot 跑完按此向用户汇报）
读 `output/daily_macro_<date>.md` 与 `.json`，汇报：
1. **四腿各自状态与上限**：①分母主状态/标签/上限/「今天不做什么」；②减震器 budget 与激活状态、轨迹标签；③主题主导叙事 + risk_bias + 上限 + 动量主角（注明=5日动量z，**非当日涨跌**）；④NQ 动因状态 + risk_bias + 是否硬否决/压力覆盖 + 是否 binding 约束项。
2. **合成上限（combined_budget）与约束项（binding leg）**——哪条腿最紧。
3. **一致性与方向**：bias（risk_on/risk_off/mixed/none）、股市承压信号、是否有背离警示。
4. **操作基调**（如「防守优先·NQ硬否决·不接飞刀」）。

> ⚠️ 文末必须附：**「教学/数据参考，不构成投资建议。四工具交叉验证，非交易信号。」**

## 7. 解读注意事项（避免误读）
- `as_of` = **已收盘的美国现金交易日**（`America/New_York`）。规则：
  - 未传 `--date`：16:00 ET 前 → 上一美国交易日；收盘后若当天是交易日 → 当天；周末/假日 → 再往前一个交易日。
  - 显式 `--date` 必须是 `YYYY-MM-DD`；**禁止中文日期**（如「今天」）——脚本会直接报错退出。
  - 若显式日期仍是「当日未收盘」或未来日：仍可写观察文件，但 JSON/MD 会标 `incomplete_bar=true` / `non_actionable=true`，GYbot **不得当执行级信号**。
- **禁止未来回退（no lookahead）**：theme/sector/shock/denom/nq 等腿的 stale fallback，若文件 `as_of` **晚于**报告日，一律拒绝；缺失/滞后腿写入 warnings，并可能把整份报告标为 `non_actionable` / `degraded_reason`。不得把未来日期的板块 Top3 当作「今日信号」。
- **板块轮动 close/volume 对齐**：`sector_rotation_daily.py` 已与 tech_rotation 同款修复——先对齐公共 index 再 `tail(HISTORY_BARS)`，`vol` reindex 到 close；打分用 `Series.mask`，避免 yfinance 401 vs 400 长度崩溃。硬错误大声失败，不静默写出错误读数。
- 分母经 FRED last-print 语义，常比主题/减震器/NQ **滞后 1 个交易日**（良性软回退，非故障）。
- 主题若落后 >1 交易日会被判 `missing` 不参与合成上限（数据滞后，非脚本故障）。
- **动量主角 z 是 5 日动量异常度**，箭头只按 z 符号画；走平日也可显示「↑」，与当日涨跌是两回事，汇报须区分。
- 分数 60 是经验观察线，非下注指令。

## 8. 不要做的一步（与作者本机自动化的差异）
作者本机的完整自动化在生成报告后，还会 `build_desk_data.py` + `sync_desk_to_nexus.ps1` 把报告推到腾讯云 NEXUS desk（只读展示）。**这一步需要作者的 SSH 私钥（`.ssh-nexus.pem`）与云服务器，grokbot 不应执行**。grokbot 只负责生成 `output/daily_macro_*.{md,json}` 本地报告即可。

## 9. 定时触发（参考）
类 cron（GMT+8 工作日 09:15，对应上一美国交易日盘后数据）：
```
15 1 * * 1-5
```
命令体即第 4 节第 3 步的 python 调用（先算 `$DATE`）。
