# 分母状态机 (Pine v1.6) 与 Macro OS kernel 融合分析

> 路径 A(Python 移植)第一阶段交付:忠实 port + 468 天平行回测 + 与 v5 kernel 交叉验证。
> 生成物: `core/denominator_state.py`(移植模块)、`scripts/backtest_denominator_state.py`(harness)、
> `denominator_state_backtest.{csv,json,md}`(原始对照)。

## 一、核心结论(先讲结论)

1. **不能直接互相调用**——Pine 跑在 TV 沙箱、kernel 是 Python 后端,但逻辑已完整移植,NOW 两者共享同一套 FRED 数据。
2. **移植是忠实的**:Pine 用到的核心数据源 Macro OS 已全部具备(TIPS/DGS2·10·30/BEI/DTWEXBGS 重定基/HY OAS/VIX),equity/gold/vol 确认项作可选输入,缺失时对应置信投票记"弃权"、状态机核心仍完整。
3. **两者匹配率很低(确定日 23.6%),但这是结构性互补,不是 bug**:
   - **kernel = 水平/绝对值视角**(DXY≥103 触发 TIGHT、VIX≤18&HY≤300 触发 RISK_ON、红线硬触发 SQUEEZE)
   - **Pine = 斜率/变化率视角**(DXY 5日 z≥1.0 才说"美元压力"、真实利率 z≤−1.0 且长端 z≤−1.0 才说"分母端宽松")
   - 一个"高但平稳"的美元 → kernel 判 TIGHT,Pine 判"分裂"(因为没在陡动)。两者捕捉的是不同东西。

## 二、状态映射与交叉表(468 天窗口 2024-10-01→2026-07-17)

| Pine v1.6 状态 | 理论对应 kernel | 天数 |
|---|---|---|
| 分母端宽松 | RISK_ON | 46 |
| 久期压力 | TRANSITION | 74 |
| 美元压力 | TIGHT_LIQUIDITY | 76 |
| 信用传导 | LIQUIDITY_SQUEEZE | 75 |
| 分裂/未确认 | (无对应·允许无知) | **197 (42.1%)** |
| 仓位主导(覆盖) | CASH_LIQUIDATION | 0(需 equity,本移植不可得) |

交叉表(行=Pine 状态,列=kernel `rule_regime`):

| Pine \\ kernel | SQUEEZE | RISK_ON | TIGHT | TRANSITION |
|---|---|---|---|---|
| 久期压力 | 14 | 38 | 5 | 17 |
| 信用传导 | 7 | 43 | 3 | 22 |
| 分母端宽松 | 3 | 30 | 3 | 10 |
| 分裂/未确认 | 11 | 103 | 19 | 64 |
| 美元压力 | 1 | 38 | 10 | 27 |

读表:
- **kernel TIGHT 时,Pine 仅 10/32 天说"美元压力"**,其余多为"分裂"——典型"水平高、斜率平"分歧。
- **kernel RISK_ON(252 天)时,Pine 仅 30 天说"分母端宽松"**,103 天"分裂"、43+38 天看到"信用传导/美元压力"——kernel 的平静(绝对值低)≠ Pine 的宽松(需利率陡降)。
- **kernel SQUEEZE 时,Pine 误判"分母端宽松"仅 6 天**——Pine 在真实危机里很少给出"假平静",这点可信。

## 三、Pine 对 Macro OS 的增量价值(互补点)

1. **"允许无知"能力(最大增量)**:Pine 有 42% 天数输出"分裂/未确认",kernel 永远给确定性 regime。这恰是 kernel 缺的"今天看不清→别加仓"信号。
2. **传导通道标签**:Pine 把压力细分为 美元/信用/久期 三种通道,kernel 只有 TIGHT/SQUEEZE 粗粒度。这给"为什么"补了叙事层。
3. **斜率预警**:kernel 用水平阈值,对"美元/利率正在陡动但还没过水平线"视而不见;Pine 的 z≥1.0 恰好捕捉这个早期斜率。

## 四、融合建议(未实现,待回测验证)

**不要替换 kernel,也不要把 Pine 当硬门**(其阈值作者明言未回测)。建议作为**软叠加层**:

- **A. 减仓阻尼(高价值、低误报)**:当 `kernel=RISK_ON (0.80)` 且 Pine 处于活跃压力态(信用传导/美元压力/久期压力)时,把预算上限压到 0.50~0.60。这精准命中"kernel 说 go、但 Pine 看到压力斜率"的 119 天,而非把所有 RISK_ON 一刀切(那样会误伤 222/252 天,均值预算 0.582→0.456,过度)。
- **B. 证伪清单**:每天把 Pine 主状态+反对票与 kernel regime 并列,分歧日人工/自动复盘(作者自己推荐的方法)。
- **C. 通道标签透传**:把 Pine 的 美元/信用/久期 通道写进 kernel 输出,补叙事层。

## 五、必须明示的局限

- **equity/gold/vol 确认项缺失**(yfinance 未装):`stCredit` 的 equity 门槛已放宽(只需 HY z≤−1.0);置信度里 gold/equity/VIX3M 投票记"弃权"。完整置信度需补 IWM/KRE/QQQ/SOXX/GLD/VIX3M。
- **"仓位主导(覆盖)"态本移植永远发不出**(需 SPX/GLD/TLT 对冲失效 + VIX 期限倒挂检测),属已知限制。
- **Pine 阈值是拍脑袋未回测**:`z_enter=1.0 / z_dead=0.5 / cs_z5≤−1.0` 等全部暴露为 `DenominatorParams`,**未纳入冻结清单**,任何进 kernel 的打算都需先校准+回测。
- 数据 CSV 已向前补到 2024-01-01 以消除前史预热假象;v5 窗口结果因 mask 不变,但若重跑 v5 仅首 ~60 天滚动 z 略有差异。

## 六、下一步(待你拍板)

1. 装 yfinance,补 equity/gold/vol 序列,跑"完整置信度"版本。
2. 实现建议 A 的阻尼逻辑为**独立实验分支**,在日频回测里对比"带阻尼 vs 不带"的预算序列与(若有收益数据)回撤,决定是否值得进 kernel。
3. 校准 Pine 阈值(至少 `z_enter`/`z_dead`)以匹配本窗口,降低 42% 的"分裂"占比到合理水平。

> ⚠️ 以上为 AI 基于公开信息整理的框架对照研究,仅供方法学讨论,不构成投资建议或个股推荐。投资有风险,决策需谨慎。
