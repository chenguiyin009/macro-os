# Trinity OS v2.1 工程交付蓝图 (执行版 / Architecture & Execution Blueprint)

> 来源：用户于 2026-07-10 提供的《Trinity OS v2.1 完整工程蓝图》。
> 配套概念蓝图见 `docs/trinity_blueprint_v2.1.md` (理论/量化模型)。本文为"落地执行"版，含代码契约与三条铁律。
> 注意：本文代码片段为方向性示例（部分含占位，如 `diagnose` 的 `pass`、`evaluate_d_structure` 的 `# ...(实现 logic)`），落地时以 `trinity/` 现有可运行实现为准，避免破坏性重命名/重构。

---

# Trinity OS v2.1 完整工程蓝图

## 1. 核心架构设计 (Architecture Overview)

Trinity OS v2.1 采用"实时感知层 + 记忆审计层 + 归因诊断层"的三层结构，确保系统在交易时具备"防偷窥"能力，并在结算后具备"归因分析"能力。

### 系统物理分层：

1. **Market Data Gateway**: 统一接入协议，强制转化为 `List[OHLCV]` 契约。
2. **Cognition Kernel (实时)**: 包含 `StructureParser` (标量化识别) 与 `DecisionRouter` (证据路由)。
3. **Event Ledger (审计)**: 使用 JSONL 追加写入的事件溯源库，确保决策不可篡改。
4. **Attribution Engine (结算)**: 后置运行的 `OutcomeSimulator` + `PerformanceAggregator`。

---

## 2. 关键模块实现代码 (Core Implementation)

### 2.1 数据契约层 (`trinity/core/contracts.py`)

这是整个系统的血液，确保所有模块对"证据"的定义高度一致。

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional

@dataclass
class EvidenceFactor:
    module: str          # e.g., "STRUCTURE", "SPACETIME"
    factor_name: str     # e.g., "D_COMPLETENESS"
    scalar_value: float  # 0.0 - 1.0 标量化分数
    weight: float        # 因子权重
    contribution: float = 0.0 # 自动计算: scalar * weight

@dataclass
class OutcomeResult:
    entry_price: float; exit_price_fixed: float; exit_price_dynamic: float
    fixed_return: float; fixed_mfe: float; fixed_mae: float
    dynamic_return: float; dynamic_mfe: float; dynamic_mae: float
    dynamic_bars_held: int

@dataclass
class TrinityEvent:
    event_id: str; timestamp: datetime; symbol: str
    entry_index: int     # 绝对物理索引，防止未来函数污染
    decision_action: str
    factors: List[EvidenceFactor]
    outcome: Optional[OutcomeResult] = None # 追加反填收益
```

### 2.2 认知内核：形态标量化 (`trinity/core/structure.py`)

将缠论式的形态识别转换为 0.0~1.0 的连续得分。

```python
class StructureParser:
    def evaluate_d_structure(self, pivots: List[Dict]) -> Tuple[float, str, Dict]:
        # 识别 H-L-H-L 序列并依据 D2/D3 位置进行连续评分
        # 返回: 0.0-1.0 标量分，状态描述，详细 Tier
        if len(pivots) < 4: return 0.15, "低位混沌", {"tier": "LOW"}

        score = 0.60 # Skeleton Base
        # 若 D2 假反弹则惩罚，若 D3 破位则奖励
        # ...(实现 logic)
        return final_score, msg, {"tier": "HIGH" if final_score > 0.8 else "MEDIUM"}
```

### 2.3 归因闭环：绩效归因引擎 (`trinity/analytics/aggregator.py`)

这是系统自我进化的核心，利用分桶与偏相关分析挖掘 Alpha。

```python
class PerformanceAggregator:
    def diagnose(self, symbol: str) -> DiagnosticReport:
        # 1. 联立事件库与收益库 (Inner Join)
        # 2. 对每个因子进行分桶 (Equal Frequency Bucketing)
        # 3. 计算偏相关系数 (Partial Correlation)
        # 4. 执行交互效应检测 (2x2 Factorial Interaction)
        # 5. 输出优化建议 (增加/减少因子权重)
        pass
```

---

## 3. 核心执行流程与铁律 (Implementation Rules)

为防止研发过程中出现致命错误，**WorkBuddy 必须执行以下三条铁律**：

1. **延迟双指针铁律 (Delayed Two-Pointer)：**
   * **指针 1 (实时流)：** 决策轮询必须只访问 `TemporalBuffer` 中 `visible_index` 之前的数据。
   * **指针 2 (清算流)：** 在全局回测结束后，以 `entry_index` 为锚点，通过 `OutcomeSimulator` 反向切片获取同序列收益。禁止在轮询中触碰未来索引。

2. **不可变证据链铁律 (Immutable Provenance)：**
   * `TrinityEvent` 一旦持久化，不可原地修改。所有后续的收益归因必须通过 `OutcomeEvent` 以追加方式绑定 `linked_event_id`。

3. **统计显著性铁律 (Statistical Significance)：**
   * 因子 Alpha 排名在样本量少于 10 条事件时，必须返回 `(样本不足，置信度低)` 警示，禁止直接触发权重建议，防止过度优化。

---

## 4. WorkBuddy 执行清单 (Action Items)

1. **初始化环境：** 创建 `trinity/` 工程，按上述蓝图建立 `core/`、`simulation/`、`analytics/` 子目录。
2. **核心迁移：** 将 `contracts.py` 作为首个提交，确保所有模块的数据契约对齐。
3. **实现 parser：** 重点开发 `StructureParser`，要求能够产出包含 `tier` (LOW/MEDIUM/HIGH) 的完整元数据。
4. **建立闭环：** 执行 `replay.py`，必须通过 3 条铁律断言（物理引用地址一致性、时间轴因果一致性、未来数据截断）。

---

## 5. 与现有 `trinity/` 实现的对应关系 (落地备注)

为避免破坏性重构，落地时优先复用现有模块而非新建重名模块：

| 蓝图要素 | 现有可运行实现 | 备注 |
|---|---|---|
| `EvidenceFactor` | `trinity/core/engine.py::EvidenceFactor` | 字段为 `module/factor/value/weight/contribution` |
| `OutcomeResult` | `trinity/outcome.py::OutcomeResult` | 含 `fixed_return/fixed_mfe/fixed_mae` 等 |
| `PerformanceAggregator.diagnose()` | `trinity/aggregator.py::PerformanceAggregator.diagnose()` | 当前无 `symbol` 参数 |
| `TemporalBuffer` (延迟双指针) | `trinity/replay.py::TemporalBuffer` | 已实现 `visible()` 前缀 + 越界 `IndexError` |
| `Event Ledger` (不可变) | `trinity/ledger.py::EventSourcingTracker` | 追加写入，无原地 mutation API |
| `StructureParser.evaluate_d_structure` | `trinity/structure_parser.py` / `trinity/core/engine.py` | 已存在 |
| `replay.py` 三铁律断言 | `tests/test_replay.py` + 新增 `TestIronLaws` | Law #1/#2 已由代码实现，补断言锁定 |

**铁律落地状态（截至本蓝图接收时）：**
- 铁律 #1 延迟双指针：代码已落地（`TemporalBuffer`），补回归断言。
- 铁律 #2 不可变证据链：代码已落地（`EventSourcingTracker` 快照式 record），补回归断言。
- 铁律 #3 统计显著性：需在 `PerformanceAggregator.diagnose()` 增加 `count < 10` 守卫（实现中）。
