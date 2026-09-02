# Grokbot 交接文档 · 标普板块轮动日频报告

> 本文件供 **grokbot（或其它外部调度器）** 从 GitHub 拉取并运行 `sector_rotation_daily.py` 使用。
> 人类阅读版说明见正文，末尾附「运行命令」与「汇报口径」可直接复制。

## 1. 这个任务是什么
每周一至周五（美国交易日）盘前，生成最新一个美国交易日的**标普板块资金轮动**读数与八条归因剧本报告。
脚本完整复刻 TradingView 上 `v1.3m` Pine 指标的打分引擎，输入为 yfinance 日线数据（SPY/XLK/XLF/XLY/XLP/XLV/XLE/XLI/XLB/XME/IWM/QQQ/TLT/HYG/GLD/KRE 等），输出到 `output/`。

## 2. 前置依赖（grokbot 环境必须满足）
| 依赖 | 说明 |
|---|---|
| Python 3.10+ | 建 venv 即可，无需系统级安装 |
| numpy / pandas / yfinance | `pip install numpy pandas yfinance` |
| 网络 | 需能访问 yfinance（ Yahoo 数据）。无本地代理的云机用 `SECTOR_PROXY=off` 直连 |
| 工作目录 | 必须在仓库根（`macro-os/`）下运行，脚本靠 `parents[2]` 定位 `output/` 与缓存目录 |

## 3. 代理开关（关键，避免卡死）
脚本默认强行注入 `HTTPS_PROXY=http://127.0.0.1:7890`（作者本机代理）。外部云机没有这个代理会拉取失败。
通过环境变量 `SECTOR_PROXY` 控制（向后兼容，作者本机不设也能照常工作）：

| SECTOR_PROXY 取值 | 行为 |
|---|---|
| 未设置 | 默认 `127.0.0.1:7890`（作者本机） |
| `off` / `none` / `0` / `false` / `no` / 空串 | **完全不设代理**（grokbot 云机用此项） |
| `http://host:port` | 使用指定代理 |
| 已设 `HTTPS_PROXY`/`HTTP_PROXY` | 以环境既有代理为准，不被覆盖 |

> 断网且首次运行（无本地缓存）会直接 `exit 2`（报"数据不完整"），不会静默成功。

## 4. 从 GitHub 下载并运行（完整流程）
```bash
# 1) 克隆专用分支（已含 SECTOR_PROXY 开关，整仓克隆以保证路径层级正确）
git clone -b sector-rotation-stable https://github.com/chenguiyin009/macro-os.git
cd macro-os

# 备注：若还要跑「每日宏观四腿」报告，用统一分支 daily-macro-stable 一支即可覆盖两个任务
# （该分支已含 sector_rotation_daily.py + daily_macro_consolidated.py 及其子脚本）。
# 详见 grokbot_daily_macro_handoff.md。

# 2) 装依赖
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install numpy pandas yfinance

# 3) 运行（关掉本机代理默认值；--report-dir 指定输出落点）
SECTOR_PROXY=off python scripts/sector_rotation_daily.py --report-dir ./output
```
单文件 raw 下载版（不推荐，首跑无缓存、断网即失败）：
```bash
curl -O https://raw.githubusercontent.com/chenguiyin009/macro-os/sector-rotation-stable/scripts/sector_rotation_daily.py
SECTOR_PROXY=off python sector_rotation_daily.py --report-dir ./output
# 注意：单文件下载时 parents[2] 指向错误根目录，必须带 --report-dir
```

## 5. 可选参数
- `--date YYYY-MM-DD`：回溯指定日期（默认 = 最新可用美国交易日）
- `--force-refresh`：跳过缓存强制拉网
- `--report-dir <绝对路径>`：改输出目录

退出码：`0`=成功；`2`=数据不完整（如 SPY 拉不到，已内置重试 1 次仍失败）。

## 6. 输出文件
运行成功后生成两份（日期 = `as_of` 最新美国交易日）：
- `output/sector_rotation_<date>.md` —— 人类阅读
- `output/sector_rotation_<date>.json` —— 机器解析

## 7. 汇报口径（grokbot 跑完按此三项回报用户）
读 `output/sector_rotation_<date>.md` 与同名 `.json`，汇报：
1. **基准 SPY 状态与分数**（如「分数 28 · 资金离场」，含绝对收益/广度）
2. **分数最高的 2–3 个板块** 与 **最低的 1–2 个板块**
3. **分数最高的归因剧本** 及其核心信号

> ⚠️ 文末必须附：**「教学/数据参考，不构成投资建议。」**

失败处理：若拉取失败，**只向用户汇报错误，不要重试超过一次**。

## 8. 解读注意事项（避免误读）
- `as_of` 永远 = 最新美国交易日。周一跑出的是上周五；距今天 >4 天脚本会 warning（长假/休市）。
- 广度低 ≠ 马上崩：如「SPY 分数 28 · 资金离场 · 广度 18%」是广谱降风险信号，脚本只做统计描述，不下单。
- 缓存回退识别：JSON 中 `data_source.from_cache` 非空即非实时，汇报时注明新鲜度。
- 分数 60 是经验观察线，非下注指令。

## 9. 定时触发（参考）
类 cron（GMT+8 工作日 06:30，对应上一美国交易日盘后数据）：
```
30 6 * * 1-5
```
命令体即第 4 节第 3 步的 python 调用。
