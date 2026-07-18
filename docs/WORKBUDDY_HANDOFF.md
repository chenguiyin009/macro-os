# Macro OS 交接说明

这份文档是给 WorkBuddy 的接手说明，目标是让他在最短时间内看懂项目结构、找到入口、知道配置在哪、以及接下来该优先检查什么。
如果后面要继续协作，优先更新这份文档，而不是重新口头说明一遍。

## 项目定位

Macro OS 是一个围绕宏观信号、决策打分、风险控制、仓位管理和外部数据桥接的自动化系统。

它的核心不是单点策略，而是一条完整链路：

1. 收集外部数据和信号
2. 做特征与状态判断
3. 通过 decision kernel 产出决策
4. 做风险、仓位和约束处理
5. 通过适配器对接 TradingView、Feishu、Futu 等外部系统

## 当前状态

1. 当前仓库已经有一份可用的交接文档，也已经加入 `docs/_INDEX.md`。
2. 本次整理使用的协作分支是 `codex/workbuddy-handoff`。
3. 这个分支已经推到 GitHub，并创建了 PR。
4. 如果你要继续做新的改动，先看这份文档，再决定要不要动代码。

## 先读这些

接手时建议按这个顺序看：

1. `README.md`
2. `docs/_INDEX.md`
3. `docs/ARCHITECTURE.md`
4. `docs/USAGE.md`
5. `docs/MACRO_OS_STATE.md`

如果想快速理解系统主线，再补看：

1. `docs/PIPELINE.md`
2. `docs/DECISION_KERNEL.md`
3. `docs/DIVERGENCE_ENGINE.md`
4. `docs/CDP_BRIDGE.md`
5. `docs/CONFIGURATION.md`

## 先看哪里

如果你只想用最短时间恢复上下文，建议直接按这个顺序看：

1. `docs/WORKBUDDY_HANDOFF.md`
2. `README.md`
3. `docs/_INDEX.md`
4. `runtime/main.py`
5. `runtime/orchestrator.py`
6. `core/decision_kernel.py`
7. `core/pipeline.py`

## 关键入口

优先关注这些运行入口和主流程文件：

1. `runtime/main.py`
2. `runtime/orchestrator.py`
3. `runtime/scheduler.py`
4. `runtime/macro_core.py`
5. `runtime/gateway_app.py`
6. `runtime/portfolio_manager.py`

这些文件基本决定了系统怎么跑、怎么调度、怎么接外部输入，以及怎么把决策落到执行层。

## 核心逻辑

下面这些模块最值得优先看：

1. `core/decision_kernel.py` - 决策核心
2. `core/pipeline.py` - 数据到决策的流水线
3. `core/scoring.py` - 打分逻辑
4. `core/regime.py` - 市场状态/风格识别
5. `core/exposure_engine.py` - 敞口与仓位引擎
6. `core/exposure_dampener.py` - 风险降档
7. `core/features.py` - 特征构造
8. `core/schemas.py` - 共享数据结构
9. `core/divergence/` - 背离相关逻辑
10. `core/macro/` - 宏观相关模块
11. `core/portfolio/reconciliation.py` - 组合对账
12. `core/gateway/gateway_core.py` - 网关侧核心

## 常用命令

这些命令适合在接手后快速确认状态：

1. `git status -sb` - 看当前分支和工作区状态
2. `git branch -a` - 看本地和远端分支
3. `git log --oneline -10` - 看最近提交
4. `node --test tests/test_decision_kernel.py` - 快速验核心逻辑
5. `node --test tests/test_runtime_main.py` - 快速验主入口

如果要做更完整的回归，再按项目现有测试集跑一轮。

## 外部适配

如果要排查“数据进不来”或“消息发不出去”，先看这些：

1. `adapters/tradingview.py`
2. `adapters/feishu.py`
3. `adapters/futu.py`
4. `adapters/vault.py`

## 配置位置

项目配置主要在这里：

1. `config/settings.py`
2. `config/config_loader.py`
3. `config/thresholds.yaml`
4. `config/watchlist.yaml`
5. `config/portfolio.yaml`
6. `config/hard_constraints.yaml`
7. `config/field_mappings.yaml`
8. `config/sector_policy.yaml`

如果 WorkBuddy 要改行为，不要先动代码，先确认对应配置是不是已经能覆盖需求。

## 脚本

这些脚本适合用来做验证、回放和配置检查：

1. `scripts/validate_ledger.py`
2. `scripts/run_v46_pipeline.py`
3. `scripts/run_replay.py`
4. `scripts/pull_global_sentinel.py`
5. `scripts/validate_macro_config.py`
6. `scripts/kernel_hardening_test.py`

## 测试

主要测试集中在：

1. `tests/test_runtime_main.py`
2. `tests/test_orchestrator_v5_futu.py`
3. `tests/test_decision_kernel.py`
4. `tests/test_scoring.py`
5. `tests/test_regime.py`
6. `tests/test_tradingview_adapter.py`
7. `tests/test_feishu_adapter.py`
8. `tests/test_futu_adapter.py`
9. `tests/test_macro_core_allocation.py`
10. `tests/test_replay.py`
11. `tests/test_configuration_contracts.py`

## 当前状态与注意点

1. `main` 分支已经和 GitHub 上的 `origin/main` 对齐。
2. `docs/MACRO_OS_STATE.md` 目前比较简短，适合补成日常运行记录。
3. 仓库根目录里已经有一些生成物或中间产物，比如 `macro_os_ledger.db`、`tmp_cdp_output.json`、`macro_history_synthetic.csv`、`__pycache__`，后续如果要长期维护，建议确认它们是否都应该继续留在仓库里。
4. 接手前最好先跑一遍测试和关键脚本，再决定下一步改哪里。
5. 如果要继续推进到 GitHub，优先用 `git push` 推分支，再创建 PR。

## GitHub 发布流程

如果后面还要继续把改动发到 GitHub，可以直接按这个流程走：

1. 确认分支改完了。
2. `git add`、`git commit`。
3. `git push -u origin <branch-name>`。
4. 打开 PR，基于 `main` 合并。

如果 `gh auth login` 在本机卡住，可以直接用浏览器里的 GitHub 登录态完成授权，再回到命令行继续推送或建 PR。

## 给 WorkBuddy 的一句话版本

“先看 `README.md` 和 `docs/_INDEX.md`，再看 `runtime/main.py`、`runtime/orchestrator.py`、`core/decision_kernel.py` 和 `core/pipeline.py`，配置在 `config/`，适配器在 `adapters/`，测试在 `tests/`。先把运行链路和配置看明白，再决定改动点。”

## 可直接复制的交接话术

“这是 Macro OS 仓库。先读 `docs/WORKBUDDY_HANDOFF.md` 和 `docs/_INDEX.md`，确认当前分支、PR、主要入口和配置位置，再决定要改哪里。现在最重要的是保持文档同步，避免每次从头解释项目背景。”
