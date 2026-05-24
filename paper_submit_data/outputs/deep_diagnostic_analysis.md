# Deep Diagnostic Analysis

## Question

这份分析回答两个问题：

1. 为什么不同 LLM models / scaffolds 的结果差异这么大？
2. 为什么 selected LLM runs 和 non-LLM oracle-style heuristic 的差距这么大？

## Evidence and Limits

分析单位是 survival-first selected run。每个模型只保留 `run_days` 最多的 run，再用 `final_networth` 和 `total_sales` 作为 tie-break。因此这份分析适合解释当前 paper-submit selected runs 的行为差异，但不是同 seed 多次重复实验，不能做显著性检验。

Action-level diagnostics 来自 `analysis/evaluate_final_metrics.py`，只对 selected LLM runs 计算。non-LLM heuristic 的 trace-level paper metrics 另见 `selected_action_diagnostics.csv` 和 `four_stage_metrics.csv`。

## Selected Runs

| Model | Framework | Days | Final Networth | Total Sales | Sold SKUs/day | Return Ratio | Expired Ratio |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MiniMax-M2.5 | plan_and_act | 73 | -397.9498 | 23521 | 12.0822 | 0.1077 | 0.0049 |
| DeepSeek-V4-Pro | plan_and_act | 180 | 10,120.90 | 164417 | 32.5667 | 0.0858 | 0.0144 |
| GLM-5.1 | react | 60 | -2,134.36 | 7016 | 3.9667 | 0.1347 | 0.0297 |
| Kimi-K2.6 | react | 130 | 792.1929 | 86214 | 25.7308 | 0.1226 | 0.0411 |
| Qwen3.5-397B-A17B | reflection | 71 | 1,183.17 | 35622 | 26.1690 | 0.1047 | 0.0655 |
| Grok-4.3 | react | 58 | 828.3989 | 11305 | 6.7414 | 0.1028 | 0.0577 |
| GPT-5.5 | react | 180 | 24,350.98 | 136405 | 32.2722 | 0.0646 | 0.0113 |
| Non-LLM Heuristic | quality_based | 180 | 131,510.42 | 267998 | 34.4833 | 0.0201 | 0.0059 |

## Short Answer

模型间差异大的直接原因不是单一指标，而是 long-horizon compounding：survival、daily SKU coverage、supplier quality selection、price/action correction、return/expiration loss 会互相放大。一个模型即使某几天动作看起来合理，只要每天少覆盖一批 SKU、选错一部分 supplier、价格偏离长期需求，后续就会同时损失 sales、cash、reviews 和 inventory space。

和 oracle-style heuristic 的差距更大，是因为 heuristic 不是 fair LLM baseline。它是手写 quality-based policy，显式编码了 shelf assortment、supplier quality、补货节奏和批量采购策略；LLM agent 则必须通过文本 observation 和工具调用在线推断这些规律，并把 evidence 转成合法动作。

## Why Models Differ

### 1. Survival Separates Stable Operators from Early-Failure Policies

Selected LLM survival range 是 58-180 天，均值 107.4 天。DeepSeek-V4-Pro / plan_and_act 和 GPT-5.5 / react 达到 180 天，Kimi-K2.6 / react 达到 130 天；MiniMax、Qwen、GLM、Grok 的 selected runs 只到 58-73 天。RetailBench 的资金、库存、评论和供应链状态会跨天累积，所以早期小错误会转成后期现金流压力。

### 2. SKU Coverage Drives Sales Scale

Selected LLM total sales range 是 7016-164417，daily sold SKU coverage range 是 3.9667-32.5667。长 survival runs 通常每天覆盖接近 30 个 sold SKUs；短 horizon runs 常常只有个位数到十几个。这说明模型差异不只是 supplier 选择，而是每天是否能把足够多的相关 SKU 纳入补货、调价和监控循环。

### 3. More Evidence Does Not Guarantee Better Actions

Action diagnostics 显示 query depth 往往不低，但 action correction 仍偏低。GPT-5.5 / react 的 query depth 是 0.992，action correction 是 0.444，是 selected LLM 中最强；DeepSeek-V4-Pro / plan_and_act 的 query depth 是 0.928，但 action correction 只有 0.271；MiniMax-M2.5 / plan_and_act 的 query depth 是 0.785，action correction 只有 0.176。这说明主要瓶颈不只是有没有查信息，而是能否把查到的信息稳定转成 supplier、quantity 和 price actions。

### 4. Supplier Quality Selection Is a Major Gap

Raw supplier-quality diagnostics 更直接。selected LLM 的 raw quality top-1 hit 只有约 0.125-0.346；quality ratio 约 0.543-0.798。也就是说，LLM 经常没有选中隐藏 raw quality 最优的 supplier，而 supplier quality 会通过 return rate、reviews、future demand 和 cash flow 继续传导。

### 5. Tool Use Is Not Monotonic

Tool calls/day 和 survival 在 selected runs 上有正相关，但它不是充分条件。Grok-4.3 / react 的 tools/day 低、query depth 也低，sales coverage 明显不足；但高工具使用也不必然产生高 performance，因为 action correction 和 supplier quality selection 才决定工具信息是否变成有效策略。

## Descriptive Correlations

这些相关性只基于 7 个 selected LLM runs，用来辅助解释模式，不用于显著性判断。

| Metric | Corr with Survival Days |
| --- | --- |
| Final Networth | 0.8198 |
| Total Sales | 0.9878 |
| Sold SKUs/day | 0.8498 |
| Return Ratio | -0.6964 |
| Expired Ratio | -0.5153 |
| Stockout Ratio | 0.5074 |
| Tools/day | 0.7938 |

Action-level correlations 同样是 descriptive。这里 action correction 与 final networth 的相关性最高，说明动作转化质量比单纯 query depth 更接近最终经营结果。

| Metric | Corr with Final Networth | Corr with Survival Days |
| --- | --- | --- |
| Query Depth | 0.6213 | 0.8112 |
| Action Correction | 0.9595 | 0.8634 |
| Strategy Consistency | 0.3453 | 0.6537 |
| Raw Quality Top-1 Hit | 0.6156 | 0.3915 |
| Quality Regret | -0.4219 | -0.0965 |
| Quality Ratio | 0.4228 | 0.0982 |
| Order Qty / Avg Daily Sales | -0.0009 | 0.0924 |
| Price Distance (%) | -0.3129 | -0.5058 |

## Action-Level Diagnostics

| Model | Framework | QDepth | Action Corr | Strategy Cons | Raw Quality Top1 | Quality Regret | Quality Ratio | Price Dist % |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MiniMax-M2.5 | plan_and_act | 0.7852 | 0.1759 | 0.3660 | 0.1404 | 1.7237 | 0.6435 | 26.9893 |
| DeepSeek-V4-Pro | plan_and_act | 0.9279 | 0.2705 | 0.5361 | 0.2326 | 1.6407 | 0.6604 | 25.4611 |
| GLM-5.1 | react | 0.8333 | 0.1646 | 0.4740 | 0.3191 | 0.9709 | 0.7980 | 36.4941 |
| Kimi-K2.6 | react | 0.9052 | 0.2453 | 0.4202 | 0.1254 | 2.2025 | 0.5430 | 22.0675 |
| Qwen3.5-397B-A17B | reflection | 0.7552 | 0.1617 | 0.2815 | 0.1688 | 1.8637 | 0.6140 | 26.4475 |
| Grok-4.3 | react | 0.5660 | 0.1667 | 0.2507 | 0.1408 | 1.7652 | 0.6330 | 110.7926 |
| GPT-5.5 | react | 0.9922 | 0.4444 | 0.4337 | 0.3465 | 1.0715 | 0.7775 | 17.9164 |

## Why the Gap to Oracle-Style Heuristic Is Large

### 1. The Heuristic Encodes a Stable Operating Policy

Non-LLM heuristic 的 final networth 是 131,510.42，total sales 是 267998，return ratio 是 0.0201，expired ratio 是 0.0059。它直接编码 quality-based procurement、补货强度和 shelf assortment，不需要从文本历史里在线归纳策略。

### 2. Equal Survival Does Not Mean Equal Operations

DeepSeek-V4-Pro / plan_and_act 和 GPT-5.5 / react 都能活到 180 天，但 final networth 仍分别低 heuristic 121,389.51 和 107,159.43。这说明 LLM 的主要差距不只是能否活到最后，还包括利润率、supplier quality、价格设定和库存周转效率。

### 3. Lower Stockout Ratio Can Be Misleading

Heuristic 的 stockout ratio 很高，但它同时有最高 total sales 和最高 networth。这意味着它可能在高吞吐、lean inventory 下承受 stockout；相反，一些 LLM runs 的 stockout ratio 较低，可能只是因为 SKU coverage 和 demand activation 太弱，不能直接解释为库存管理更好。

### 4. Quality Loss Compounds Over Time

Selected LLM return ratio range 是 0.0646-0.1347，expired ratio range 是 0.0049-0.0655，均高于 heuristic 的 0.0201 return ratio 和 0.0024 expired ratio。在 RetailBench 中，这些不是一次性损失：returns 会影响 revenue 和 reviews，expiration 会占用采购资金和库存空间，supplier quality 会影响后续 demand。

## Gap to Heuristic

Gap 定义：`heuristic - selected LLM`；return/expired gap 定义为 `selected LLM - heuristic`。

| Model | Framework | Days | Days Gap | Networth Gap | Sales Gap | Return Over Oracle | Expired Over Oracle |
| --- | --- | --- | --- | --- | --- | --- | --- |
| MiniMax-M2.5 | plan_and_act | 73 | 107 | 131,908.37 | 244477 | 0.0876 | -0.0009 |
| DeepSeek-V4-Pro | plan_and_act | 180 | 0 | 121,389.51 | 103581 | 0.0657 | 0.0086 |
| GLM-5.1 | react | 60 | 120 | 133,644.78 | 260982 | 0.1146 | 0.0239 |
| Kimi-K2.6 | react | 130 | 50 | 130,718.22 | 181784 | 0.1025 | 0.0352 |
| Qwen3.5-397B-A17B | reflection | 71 | 109 | 130,327.24 | 232376 | 0.0846 | 0.0596 |
| Grok-4.3 | react | 58 | 122 | 130,682.02 | 256693 | 0.0827 | 0.0518 |
| GPT-5.5 | react | 180 | 0 | 107,159.43 | 131593 | 0.0444 | 0.0055 |

## Paper-Level Interpretation

可以在论文中保守表述为：RetailBench 区分了 short-term action execution 和 long-horizon operational competence。当前 LLM agents 的差异主要来自能否持续覆盖足够多 SKU、把 evidence 转换为稳定动作、并控制 supplier quality 带来的 returns/expiration loss。Non-LLM heuristic 的作用是显示环境仍有明显 headroom，而不是作为公平模型 baseline 或严格 upper bound。

不建议写成：oracle 证明了最优上界、某模型显著优于另一模型、或差异完全来自模型能力。当前数据更支持 descriptive diagnosis 和 headroom framing。
