---
tags: [macro-os, data, fallback, research]
---

# 宏观数据缺字段互补填充规范

## 目的

这份文档说明 Macro OS 在研究层如何处理“单一数据源缺字段”的情况。

目标只有一个：

1. 尽量让研究周报和结构化快照保持完整
2. 只在缺失字段上做补全，不覆盖已有高质量值
3. 不把研究层的补全逻辑写进 `decision_kernel` 或预算链路

换句话说，这是一条 **研究层补齐链路**，不是控制层重写链路。

## 适用范围

该规范主要作用于以下入口：

1. [`scripts/generate_funding_price_weekly.py`](../scripts/generate_funding_price_weekly.py)
2. [`adapters/fred.py`](../adapters/fred.py)
3. [`adapters/yfinance_macro.py`](../adapters/yfinance_macro.py)
4. [`adapters/macro_composite.py`](../adapters/macro_composite.py)

适用场景包括：

1. FRED 某些系列暂时抓不到
2. yfinance 某些代理字段暂时缺失
3. 单一来源有覆盖空窗，但另一个来源能提供互补字段
4. 周报生成时希望避免 `None` 出现在关键研究列里

## 核心原则

### 1. 先保留，再补齐

如果主源已经提供了值，默认不覆盖。

补全只发生在：

- 主源字段为 `None`
- 备源字段非 `None`

### 2. 高质量字段优先

研究层里，真实研究序列优先于代理序列。

典型优先级是：

1. FRED 真实系列
2. yfinance 代理系列
3. last-good cache

### 3. 只做研究层填充

补全后的结果可以写入研究快照和周报。

但它不应该：

- 直接改写 kernel 预算
- 直接把 `Q1` 映射成 `LIQUIDITY_SQUEEZE`
- 直接污染红线折叠结果

## 数据源职责

### FRED

FRED 负责提供偏“研究级”的真实宏观序列，尤其适合这些字段：

- `tips_yield`
- `nominal_10y`
- `nominal_30y`
- `nominal_2y`
- `hy_credit_spread`
- `vix`
- `bei_10y`

在实现上，`adapters/fred.py` 使用扩展 series map，把这些字段都纳入可抓取集合。

### yfinance

yfinance 负责提供可用的市场代理字段，适合作为 FRED 的补充。

它更偏向“快速 proxy”，例如：

- `dxy`
- `gold`
- `nominal_10y`
- `nominal_30y`
- `nominal_2y` 的代理值
- `vix`

注意：

- `^TNX` 和 `^TYX` 是名义利率水平，不是 TIPS 真实利率
- `^IRX` 只能作为 `nominal_2y` 的代理，不应当被误写成真正的 2Y UST
- `TIP` 和 `HYG` 只能作为软 proxy，不应伪装成真实 OAS 或真实 TIPS

### Composite

[`adapters/macro_composite.py`](../adapters/macro_composite.py) 负责把多源快照合并成一个研究快照。

当前合并顺序是：

1. TV
2. yfinance
3. FRED
4. cache

在研究关键字段上，FRED 会被优先保留。

## 互补填充规则

### 规则 A: 缺什么补什么

主源字段为空时，才从备源拿值。

不会做“后来的字段把前面的字段全覆盖”。

### 规则 B: 只补非空值

如果备源本身也是空值，就保持空。

### 规则 C: 补完后再派生

补齐原始字段后，再统一做派生计算，比如：

- `bei_10y = nominal_10y - tips_yield`

这样可以避免“先派生、后补齐”导致的假空值。

### 规则 D: 研究快照和周报共享同一套值

周报里看到的数值，应该和结构化 snapshot 保持一致。

## 当前实现路径

### 1. 周报生成脚本

[`scripts/generate_funding_price_weekly.py`](../scripts/generate_funding_price_weekly.py) 现在支持：

- `fred` 模式下，FRED + yfinance 互补
- `yfinance` 模式下，yfinance + FRED 互补
- `composite/auto` 模式下，多源快照合并

其核心行为是：

1. 先拿主源
2. 再拿备源
3. 只合并缺失字段
4. 最后补派生字段

### 2. FRED 适配器

[`adapters/fred.py`](../adapters/fred.py) 使用扩展 series map，避免只抓最小集合。

这一步解决了一个关键问题：

- 以前某些字段没有进入抓取集合，所以即便 FRED 里有数据，也会在 schema 里落成 `None`
- 现在 `DGS2`、`T10YIE`、`DTWEXBGS` 这类字段可以进入统一特征层

### 3. yfinance 适配器

[`adapters/yfinance_macro.py`](../adapters/yfinance_macro.py) 提供代理字段补位。

它的定位不是“替代真实宏观数据”，而是：

- 在数据缺口期保住周报完整性
- 给研究层提供连续时间序列

## 字段解释

### `nominal_2y`

优先来源：

1. FRED `DGS2`
2. yfinance `^IRX` 代理

如果两者都没有，就保留空值。

### `bei_10y`

这个字段不直接依赖外部源，而是由以下字段派生：

`bei_10y = nominal_10y - tips_yield`

只要 `nominal_10y` 和 `tips_yield` 都存在，就可以补出来。

### `dxy`

优先来源：

1. yfinance `DX-Y.NYB`
2. FRED 代理序列（如果合并链路里有对应值）

## 验证方式

当前这套逻辑已经通过以下两类验证：

1. 单测
2. 周报实跑

推荐检查的命令：

```bash
python -m pytest tests/test_fred_adapter.py tests/test_weekly_pipeline.py tests/test_funding_price_quadrant.py tests/test_dry_run_funding_research.py -q
python scripts/generate_funding_price_weekly.py --source fred --as-of 2026-07-16
```

生成结果会落到：

- [`data/research/funding_price_week_2026-07-13.json`](../data/research/funding_price_week_2026-07-13.json)
- [`docs/research/2026-07-17-funding-price-weekly-auto.md`](./2026-07-17-funding-price-weekly-auto.md)

## 运行约定

1. 研究层允许补全
2. kernel 层不允许被补全逻辑污染
3. 任何代理值都要保留单位边界
4. 任何真实系列都要尽量优先于代理系列

## 交接提示

如果后续 WorkBuddy 继续维护这块，优先看：

1. [`scripts/generate_funding_price_weekly.py`](../scripts/generate_funding_price_weekly.py)
2. [`adapters/fred.py`](../adapters/fred.py)
3. [`adapters/yfinance_macro.py`](../adapters/yfinance_macro.py)
4. [`adapters/macro_composite.py`](../adapters/macro_composite.py)
5. [`docs/research/README.md`](./research/README.md)

这五个位置基本就能把“为什么字段会缺、怎么补、补完写到哪”看清楚。
