# Parent: please UpdateRoutine（子代理无 UpdateRoutine 权限）

Do not change production C / 四腿 min / pine defaults. Do not push.
Calibration suggestions only — never mutate scores.

## New folder（若不存在则新建）

建议新建例行文件夹，例如名称：`校准复盘`（或挂到现有「晨报」folder 旁）。
若 parent 已知晨报 folder id，可与 09:15/09:16 同 folder 或新建；**本草稿不假定 folder id**。

## Routine

- name: `日报校准 09:35`
- schedule: `35 9 * * 1-5`（用户本地 Etc/GMT-8 / UTC+8）
- folder: 新建 `校准复盘`（或 parent 指定）

### prompt

```
为 G y 做「日报校准复盘」：比对前一报告日预判 vs 次日完整美股现金收盘实际。覆盖四腿+科技+标普三份。回复中文。

硬约束：不改生产 C / 四腿 combined min / pine 默认 / 任何打分；校准只出建议；不 push；不改 Obsidian 归档根路径以外的生产脚本。

实现仓：/workspace/calibration。用 /workspace/macro-os/.venv（若无则 python3 + pandas）。环境 SECTOR_PROXY=off。

步骤：
1. 运行 `SECTOR_PROXY=off python /workspace/calibration/scripts/calibrate_daily.py`（不要默认 --force）。脚本会取「最新完整美股现金收盘」的前一美股交易日作为预判 as_of，用已有 output JSON + bar 缓存（必要时经 bar_sources 补 bar）。
2. 若 stdout 含「校准无新数据」：只向本聊天回复「校准无新数据」并结束。
3. 否则读 staging MD：`/workspace/obsidian-staging/GYbot日报/校准/<prediction_as_of>.md`，以及同 as_of 的 `output/calibration_<as_of>.json`。
4. 聊天要点（短）：预判 as_of → 实际 1D 日；命中 1–2 条；落空 1–2 条；可改点（明确写「建议、不改分」）；四腿 posture/binding、科技强弱与对A半导门控、标普 pine Top3（C 权重只读展示）。结尾：教学/数据参考，不构成投资建议。

输入源（只读）：
- 四腿：/workspace/macro-os/output/daily_macro_<as_of>.json（plain_advice.posture、combined_risk_budget.binding）
- 科技：/workspace/tech-rotation/output/tech_rotation_<as_of>.json（strongest/weakest、a_share_mapping）
- 标普：/workspace/tradingview-desk/rotation_results/daily_abc_*.json（spy.top3 + strategies id=C weights）；缺则 sector_rotation JSON Top3

不要：改评分公式、改 Book C、改 pine 默认、跑完整晨报重算、push git、向第三方发信。

结果发给：本 Grok Bot 聊天里的用户。无新数据时只说「校准无新数据」。
```
