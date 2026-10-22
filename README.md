# GYbot 日报校准（v1）

每天比对**前一报告日预判** vs **次日完整美股现金收盘**实际，覆盖：

1. 四腿宏观（posture / combined binding）
2. 科技轮动（strongest / weakest + 对A半导门控）
3. 标普 pine Top3 + C 权重（只读，不改生产）

## 硬约束

- **不改**生产 C / 四腿 combined min / pine 默认 / 任何打分
- 校准**只出建议**
- **不 push**

## 运行

```bash
cd /workspace/calibration
SECTOR_PROXY=off /workspace/macro-os/.venv/bin/python scripts/calibrate_daily.py
# 指定预判日：
SECTOR_PROXY=off /workspace/macro-os/.venv/bin/python scripts/calibrate_daily.py --as-of 2026-10-08 --force
```

无新 1D bar 或 staging 已有同窗口 → 打印 `校准无新数据`。

产出：

- JSON：`output/calibration_YYYY-MM-DD.json`（YYYY-MM-DD = 预判 as_of）
- MD：`/workspace/obsidian-staging/GYbot日报/校准/YYYY-MM-DD.md`

## 规则摘要

| 规则 | 命中条件 |
|------|----------|
| 四腿方向 | 偏多↔涨、偏空/防守↔跌；观望且 \|1D\|≤0.8% |
| 科技相对强弱 | 强主题多数跑赢 QQQ、弱主题多数跑输 |
| 半导对A门控 | 降预期 ↔ SMH 相对 QQQ 偏弱（rel1/rel5） |
| **对A列（独立小节）** | 前日 posture/半导降预期 ↔ 次日创业板/沪深300/恒指/恒科；与美股 defend 建议分列 |
| 标普 pine Top3 | Top3 多数次日跑赢 SPY |

## 测试

```bash
/workspace/macro-os/.venv/bin/python -m pytest -q
```

## 例行

见 `docs/routine_prompt_draft.md`（约 09:35 工作日，晨报之后）。Parent 用 UpdateRoutine 创建；本仓不自建例行。
