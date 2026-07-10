# Trinity OS: 三位一体交易系统工程蓝图 (v2.1)

> 本文档是对核心资料的最终结构化归纳，补齐了时空对称、级别嵌套传导、
> 以及高阶回测审计的所有逻辑漏洞，确保能够直接输入至研发流程。

## 1. 核心理论架构 (Theoretical Foundation)

本系统基于"均线(MA)、结构(Structure)、时空(Space-Time)"三位一体模型。

- **时空要素 (核心进阶)**：调整是否充分的判定不是简单判断三段式，而是基于"结构必完美"——上一笔高级别下跌段内，必须包含一个完整的次级别 D 型结构 (D1-D2-D3)。
- **同构性原理**：利用谢尔宾斯基三角形原理，将 J+2 到 J-2 的级别走势视为同构的"雪花生长"，核心在于精准捕捉 J-1 回抽布林带中轨后的二次确认点。

## 2. 级别嵌套传导矩阵 (J-Level Cascade)

系统采取"自上而下分析，自下而上交易"的严格传导：

| 级别 | 定义 | 核心功能 |
|---|---|---|
| J+2 | 时空基准 (月/周) | 判定宏观情绪，过滤非主涨段。 |
| J | 结构基准 (日线) | 识别波段结构 (A/B/C/D)，判定背离，寻找 D 结构拐点。 |
| J-1 | 执行基准 (60分) | 关键：回抽布林带中轨确认支撑，过滤假突破。 |
| J-2 | 战术基准 (15分) | MA55 均线共振狙击，最后扣动扳机点。 |

## 3. 关键量化审计模型 (Quantification Models)

### 3.1 时空调整评分系统 (Spacetime Score)

不再使用布尔值判定，升级为连续量化评分：

- **Time Score (时间对称)**：`1.0 - |T_curr - T_prev| / max(T_curr, T_prev)`
- **Space Score (结构完整)**：检测是否存在完整 D1-D2-D3 三段式结构，若包含则 Space Score = 0.95。
- **Total Score**：`Time_Score * 0.4 + Space_Score * 0.6`

### 3.2 结构识别解析器 (Structure Parser)

- **ZigZag 算法升级**：使用 Pivot High/Low 分型检测。
- **D 结构识别逻辑**：
  - 连续识别四个分型：H-L-H-L。
  - 验证反弹点 (D2) 低于起点 (Start H)。
  - 验证 D3 下跌点 (D3 L) 是否破位 D1 点 (D1 L)。

### 3.3 决策路由 (Decision Routing)

- **STRONG_ADD**: Macro强 + J-Struct主升 + J-1回抽确认 + Space Score > 0.75。
- **TAKE_PROFIT_T**: 突破遇阻 + 小级别背离 + 动能衰减。
- **EXIT/WAIT**: 结构破坏（顶背离 + 跌破 MA55）或 时空调整未达标。

## 4. 系统工程模块定义

### 4.1 数据网关协议 (Standard OHLCV Contract)

```python
@dataclass
class OHLCV:
    timestamp: datetime
    open, high, low, close, volume: float
```

所有接入接口（TradingView, Futu, TDX）必须强制转换为此格��。

### 4.2 证据溯源中枢 (Evidence-Based Ledger)

- **核心改变**：决策不再仅记录"买入"，而是记录"为什么买"。
- **结构化记录示例**：
  ```json
  {
    "module": "J+2",
    "factor": "MACD_HIST",
    "value": 0.82,
    "weight": 0.3,
    "contribution": 0.246
  }
  ```
- **目的**：通过 Attribution Engine 统计：哪个因子对 Alpha 的贡献最大。

## 5. 工程实施路线图 (Execution Roadmap)

### 阶段一：内核冻结 (MVP)

- Structure Parser: 完成 ZigZag 高低点识别与 D 型结构验证。
- Spacetime Engine: 完成时间对称性公式实现。
- Decision Kernel: 落实 STRONG_ADD 到 WAIT 的 6 种路由策略。

### 阶段二：回测与归因 (Replay Edition)

- Replay Engine v0.1: 实现 TemporalBuffer (防止未来函数污染)，串联所有模块。
- Outcome Simulator: 接入回测收益计算，统计因子贡献。
