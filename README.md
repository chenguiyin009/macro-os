# GYbot 日报校准（v2）

每天比对**前一报告日预判** vs **次日完整美股现金收盘**实际，覆盖：

1. 四腿宏观（posture / combined binding）——官方窗口仍是次日 1D
2. 科技轮动（按*状态*选出的明确强弱主题，含可测篮子 + 对A半导门控完整性/5D结果）
3. 标普 pine Top3 + C 权重（只读，不改生产）

## 硬约束

- **不改**生产 C / 四腿 combined min / pine 默认 / 任何打分 / 半导硬门控
- 校准**只出建议**
- **不 push**

## 运行

```bash
cd /workspace/calibration
SECTOR_PROXY=off /workspace/macro-os/.venv/bin/python scripts/calibrate_daily.py
# 指定预判日：
SECTOR_PROXY=off /workspace/macro-os/.venv/bin/python scripts/calibrate_daily.py --as-of 2026-10-08 --force
# 只回填已满 5 个美股交易日的官方科技/半导结果：
SECTOR_PROXY=off /workspace/macro-os/.venv/bin/python scripts/calibrate_daily.py --backfill-5d-only
```

无新 1D bar 或 staging 已有同窗口的 v2 笔记 → 打印 `校准无新数据`。例行仍会尝试把已满 5 日窗口、但仍停在「观察」的旧 as_of 回填为官方结果。

产出：

- JSON：`output/calibration_YYYY-MM-DD.json`（YYYY-MM-DD = 预判 as_of，schema `gybot.calibration.v2`）
- MD：`/workspace/obsidian-staging/GYbot日报/校准/YYYY-MM-DD.md`

## 规则摘要

| 规则 | 官方命中条件 |
|------|----------------|
| 四腿方向 | 偏多↔涨、偏空/防守↔跌；观望且 \|1D\|≤0.8%。窗口仍是次日 1D，不因本版改口径。 |
| 科技相对强弱 | **按状态而非 ETF 分数排名**：强=确认进入/拥挤主升/早期轮动，弱=资金撤出/派发/撤出；排除中性观察。篮子在成员可测时用等权收益，无法测则跳过该主题、不改用无关 ETF。无任何可测明确强弱 → 弃权。**官方窗口为 5 个交易日相对 QQQ**；1D 只记观察，不计命中/落空，也不据此写「拥挤惩罚过拟合」。5 日齐了会回填该 as_of 的 JSON/MD。 |
| 半导对A门控完整性 | 同日核对：按生产规则（落后均分≥15 或 rel5<0 → 降级；偏多须确认进入且 rel5≥0）映射是否用对。与次日 SMH 涨跌无关；不改生产硬门控。 |
| 半导对A门控结果 | **官方仅在 5D SMH-QQQ 就绪后**：仅当 5 日相对明确反向（默认 \|rel5\|≥2%）才算落空。1D rel1 只观察，不算漏判。与科技同一套 5D 回填。 |
| **对A列（独立小节）** | 前日 posture/半导降预期 ↔ 次日创业板/沪深300/恒指/恒科；与美股 defend 建议分列。刷新缓存时传入次日 need_through；仍缺数据时记录抓取失败/假期/空数据等具体原因，不再只写「跳过」。 |
| 标普 pine Top3 | Top3 多数次日跑赢 SPY（笔记·不驱动 C / pine）。 |

## 测试

```bash
/workspace/macro-os/.venv/bin/python -m pytest -q
```

## 例行

见 `docs/routine_prompt_draft.md`（约 09:35 工作日，晨报之后）。Parent 用 UpdateRoutine 创建；本仓不自建例行。默认入口会在写当日 1D 笔记之外，回填已满 5 日窗口的官方科技/半导结果。
