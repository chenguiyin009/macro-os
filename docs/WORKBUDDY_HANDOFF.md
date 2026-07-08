# Macro OS 交接说明

这份文档是给 WorkBuddy 的接手说明，目标是让他在最短时间内看懂项目结构、找到入口、知道配置在哪、以及接下来该优先检查什么。

## 项目定位

Macro OS 是一个围绕宏观信号、决策打分、风险控制、仓位管理和外部数据桥接的自动化系统。

它的核心不是单点策略，而是一条完整链路：

1. 收集外部数据和信号
2. 做特征与状态判断
3. 通过 decision kernel 产出决策
4. 做风险、仓位和约束处理
5. 通过适配器对接 TradingView、Feishu、Futu 等外部系统

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

## 给 WorkBuddy 的一句话版本

“先看 `README.md` 和 `docs/_INDEX.md`，再看 `runtime/main.py`、`runtime/orchestrator.py`、`core/decision_kernel.py` 和 `core/pipeline.py`，配置在 `config/`，适配器在 `adapters/`，测试在 `tests/`。先把运行链路和配置看明白，再决定改动点。”

