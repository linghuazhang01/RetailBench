# RetailBench Paper Submit Data Report

## Scope

本报告汇总 8 个模型/基线的 20 个 selected rollout runs。其中 6 个模型包含 `react`、`reflection`、`plan_and_act` 三个 framework run；剩余条目使用 `manifest.json` 中当前可用的 run。

原始 run 路径由 `manifest.json` 显式声明。LLM 条目通常指向 `/Volumes/Data` 原始日志目录；non-LLM 条目既支持 legacy `run_log`/`records_db` summary 输入，也支持包含 `tool_calls.jsonl` 与 `db/records.db` 的完整 run 目录。

当 non-LLM heuristic baseline 提供 `tool_calls.jsonl` 时，脚本从 trace 中解析经营指标和 tool-use 指标；summary-only 输入则从 summary log 中解析经营指标，并将 tool/token cost 记为 0。non-LLM 不产生 LLM token usage，因此 token/cost 字段保持为空或 0。

## Metrics

指标分组来自 `paper/latex_arr_revision/capter/experiment.tex` 和 `paper/latex_arr_revision/table/final_evaluation_summary.tex`。

### Operation

| Metric | 方向 | 定义 |
| --- | --- | --- |
| run_days | 越高越好 | episode 终止或分析窗口结束前的经营天数，越高越好。 |
| final_networth | 越高越好 | 最后一个分析日的 net worth，越高越好。 |

### Sales

| Metric | 方向 | 定义 |
| --- | --- | --- |
| total_sales | 越高越好 | 分析窗口内累计售出件数，由每日 sales_by_sku 汇总得到，越高越好。 |
| avg_daily_sold_skus | 越高越好 | 平均每天实际发生销售的不同 SKU 数量，越高表示销售覆盖更广。 |
| return_ratio | 越低越好 | 退货件数除以售出件数，越低越好。 |

### Inventory

| Metric | 方向 | 定义 |
| --- | --- | --- |
| expired_ratio | 越低越好 | 过期件数除以售出件数与过期件数之和，越低越好。 |
| stockout_days | 越低越好 | insufficient_skus 非空的天数，越低越好。 |
| stockout_ratio | 越低越好 | 缺货天数除以分析天数，越低越好。 |

### Tool Use

| Metric | 方向 | 定义 |
| --- | --- | --- |
| avg_direct_tool_calls_per_day | 越高越好 | 平均每天顶层工具调用次数，不包含 execute_code 内部触发的工具调用。 |
| avg_all_tool_calls_per_day | 越高越好 | 平均每天总工具调用次数，包含顶层工具调用与 execute_code 内部触发的工具调用。 |

### Token Cost

| Metric | 方向 | 定义 |
| --- | --- | --- |
| avg_tokens_per_day | 越低越好 | total_tokens 除以有 token usage 记录的天数。 |
| avg_cost_usd_per_day | 越低越好 | 可选的每日平均费用估计；未提供 token 单价时为空。 |

## Run Coverage

| Model | Type | Runs | Frameworks | 3-framework complete |
| --- | --- | --- | --- | --- |
| DeepSeek-V4-Pro | llm | 3 | plan_and_act, react, reflection | yes |
| GLM-5.1 | llm | 3 | plan_and_act, react, reflection | yes |
| GPT-5.5 | llm | 1 | react | no |
| Grok-4.3 | llm | 3 | plan_and_act, react, reflection | yes |
| Kimi-K2.6 | llm | 3 | plan_and_act, react, reflection | yes |
| MiniMax-M2.5 | llm | 3 | plan_and_act, react, reflection | yes |
| Non-LLM Heuristic | non_llm | 1 | quality_based | no |
| Qwen3.5-397B-A17B | llm | 3 | plan_and_act, react, reflection | yes |

## Per-Run Results

| Model | Type | Framework | Days | Final Networth | Total Sales | Sold SKUs/day | Return Ratio | Expired Ratio | Stockout Days | Direct Tools/day | Total Tools/day | Avg Tokens/day | Avg Cost/day |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MiniMax-M2.5 | llm | react | 61 | 1,788.24 | 22384 | 14.6393 | 0.1041 | 0.0028 | 50 | 19.5902 | 36.9672 | 644,314.75 | -- |
| MiniMax-M2.5 | llm | reflection | 56 | 416.9656 | 16819 | 7.7321 | 0.1041 | 0.0085 | 37 | 34.4643 | 57.3036 | 1,277,005.15 | -- |
| MiniMax-M2.5 | llm | plan_and_act | 73 | -397.9498 | 23521 | 12.0822 | 0.1077 | 0.0049 | 52 | 18.6712 | 40.1918 | 484,698.46 | -- |
| DeepSeek-V4-Pro | llm | react | 144 | -1,554.47 | 134931 | 30.1736 | 0.0968 | 0.0573 | 80 | 15.4653 | 53.7708 | 383,893.10 | -- |
| DeepSeek-V4-Pro | llm | reflection | 94 | -1,792.13 | 33593 | 25.9787 | 0.0980 | 0.0452 | 73 | 18.1596 | 58.3404 | 459,671.66 | -- |
| DeepSeek-V4-Pro | llm | plan_and_act | 180 | 10,120.90 | 164417 | 32.5667 | 0.0858 | 0.0144 | 116 | 23.2278 | 62.3500 | 508,515.65 | -- |
| GLM-5.1 | llm | react | 60 | -2,134.36 | 7016 | 3.9667 | 0.1347 | 0.0297 | 14 | 37.5833 | 39.9833 | 1,054,510.75 | -- |
| GLM-5.1 | llm | reflection | 49 | 4,619.76 | 6770 | 6.7755 | 0.1145 | 0.0086 | 23 | 51.8367 | 53.3265 | 1,151,326.44 | -- |
| GLM-5.1 | llm | plan_and_act | 56 | -2,824.78 | 739 | 1.3750 | 0.0338 | 0.0000 | 11 | 36.6071 | 37.3571 | 842,540.02 | -- |
| Kimi-K2.6 | llm | react | 130 | 792.1929 | 86214 | 25.7308 | 0.1226 | 0.0411 | 62 | 26.3923 | 45.7615 | 523,108.26 | -- |
| Kimi-K2.6 | llm | reflection | 66 | 5,902.33 | 30783 | 10.9242 | 0.1121 | 0.0002 | 31 | 58.5606 | 127.8788 | 1,026,088.92 | -- |
| Kimi-K2.6 | llm | plan_and_act | 89 | -1,996.52 | 48955 | 12.1348 | 0.0911 | 0.0016 | 58 | 26.5169 | 46.0899 | 505,149.83 | -- |
| Qwen3.5-397B-A17B | llm | react | 50 | 6,125.56 | 32874 | 26.1800 | 0.0968 | 0.0345 | 34 | 27.3400 | 72.4000 | 1,034,179.51 | -- |
| Qwen3.5-397B-A17B | llm | reflection | 71 | 1,183.17 | 35622 | 26.1690 | 0.1047 | 0.0655 | 48 | 19.8732 | 49.7606 | 686,103.11 | -- |
| Qwen3.5-397B-A17B | llm | plan_and_act | 50 | 3,820.59 | 30074 | 23.2200 | 0.1170 | 0.1055 | 29 | 28.3000 | 63.2000 | 939,459.18 | -- |
| Grok-4.3 | llm | react | 58 | 828.3989 | 11305 | 6.7414 | 0.1028 | 0.0577 | 24 | 9.5000 | 10.3621 | 144,582.11 | -- |
| Grok-4.3 | llm | reflection | 51 | 787.6653 | 20907 | 11.7255 | 0.1286 | 0.1735 | 18 | 11.6078 | 12.7647 | 176,045.66 | -- |
| Grok-4.3 | llm | plan_and_act | 12 | 22,989.16 | 372 | 1.6667 | 0.0538 | 0.0000 | 5 | 22.2500 | 23.1667 | 456,946.73 | -- |
| GPT-5.5 | llm | react | 180 | 24,350.98 | 136405 | 32.2722 | 0.0646 | 0.0113 | 137 | 17.0889 | 106.8611 | 176,260.07 | -- |
| Non-LLM Heuristic | non_llm | quality_based | 180 | 131,510.42 | 267998 | 34.4833 | 0.0201 | 0.0059 | 122 | 141.4389 | 141.4389 | -- | -- |

## Best Run by Model

默认选择规则改为 survival-first：优先选择 run days 最多的 run，再用 final networth 和 total sales 做 tie-break。这个规则更符合当前 paper-submit 目标，因为过早破产或结束的 run 不应仅凭短期 networth 被选为 best run。单 run baseline 只会选择自身。

| Model | Type | Selected Framework | Run ID | Days | Final Networth | Total Sales | Sold SKUs/day | Return Ratio | Expired Ratio | Stockout Days | Stockout Ratio | Direct Tools/day | Total Tools/day | Avg Tokens/day | Avg Cost/day | Rule |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| DeepSeek-V4-Pro | llm | plan_and_act | run_plan_and_act_deepseek-v4-pro_caec_off_fcr_off_run1 | 180 | 10,120.90 | 164417 | 32.5667 | 0.0858 | 0.0144 | 116 | 0.6444 | 23.2278 | 62.3500 | 508,515.65 | -- | max run_days, tie-break by final_networth then total_sales |
| GLM-5.1 | llm | react | run_react_glm-5.1_caec_off_fcr_off_run1 | 60 | -2,134.36 | 7016 | 3.9667 | 0.1347 | 0.0297 | 14 | 0.2333 | 37.5833 | 39.9833 | 1,054,510.75 | -- | max run_days, tie-break by final_networth then total_sales |
| GPT-5.5 | llm | react | run_react_gpt55_hard_v2_20260521_110407 | 180 | 24,350.98 | 136405 | 32.2722 | 0.0646 | 0.0113 | 137 | 0.7611 | 17.0889 | 106.8611 | 176,260.07 | -- | max run_days, tie-break by final_networth then total_sales |
| Grok-4.3 | llm | react | run_react_x-ai_grok-4.3_run1 | 58 | 828.3989 | 11305 | 6.7414 | 0.1028 | 0.0577 | 24 | 0.4138 | 9.5000 | 10.3621 | 144,582.11 | -- | max run_days, tie-break by final_networth then total_sales |
| Kimi-K2.6 | llm | react | run_react_kimi-k2.6_caec_off_fcr_off_run1 | 130 | 792.1929 | 86214 | 25.7308 | 0.1226 | 0.0411 | 62 | 0.4769 | 26.3923 | 45.7615 | 523,108.26 | -- | max run_days, tie-break by final_networth then total_sales |
| MiniMax-M2.5 | llm | plan_and_act | run_plan_and_act_MiniMax-M2.5_caec_off_fcr_off_run1 | 73 | -397.9498 | 23521 | 12.0822 | 0.1077 | 0.0049 | 52 | 0.7123 | 18.6712 | 40.1918 | 484,698.46 | -- | max run_days, tie-break by final_networth then total_sales |
| Non-LLM Heuristic | non_llm | quality_based | non_llm_hard_v2_sku2_bulk6_180_20260524 | 180 | 131,510.42 | 267998 | 34.4833 | 0.0201 | 0.0059 | 122 | 0.6778 | 141.4389 | 141.4389 | -- | -- | max run_days, tie-break by final_networth then total_sales |
| Qwen3.5-397B-A17B | llm | reflection | run_reflection_qwen3.5-397b-a17b_caec_off_fcr_off_run1 | 71 | 1,183.17 | 35622 | 26.1690 | 0.1047 | 0.0655 | 48 | 0.6761 | 19.8732 | 49.7606 | 686,103.11 | -- | max run_days, tie-break by final_networth then total_sales |

## Diagnostic Analysis

### Analysis Limits

这里的分析是 descriptive analysis：unit 是 selected rollout run，不是严格同 seed 的多次重复实验。因此它能解释当前数据中的主要差异模式，但不支持显著性检验或把差异完全归因到某一个模型能力维度。

### Why Models Differ

Survival days 是最强的第一层分化信号。按 survival-first 选择后，LLM selected runs 的 survival range 是 58-180 天，均值约 107.4 天。DeepSeek-V4-Pro / plan_and_act 和 GPT-5.5 / react 都达到 180 天；Kimi-K2.6 / react 达到 130 天；其余 selected runs 只到 58-73 天。这说明很多差异首先来自是否能长期维持现金流和库存周转，而不只是某一天的动作质量。

第二层差异来自 SKU coverage 和销售规模。能长期运行的 selected LLM runs 通常每天覆盖约 30 个 sold SKUs，并达到 136k-164k total sales；短 horizon runs 往往只有个位数到十几个 sold SKUs/day，total sales 也低一个数量级。这表明模型不是只在单个 supplier 或 price action 上有差异，而是在每天能否持续选择足够多、足够相关的 SKU 进入补货和调价集合上有明显差异。

第三层差异来自 loss channels。selected LLM runs 的 return ratio 明显高于 heuristic baseline，并且部分 runs 有较高 expired ratio；这些损失会在 long-horizon setting 中复利式影响资金、库存空间和后续采购能力。stockout ratio 需要和 sales 一起读：低 stockout 不一定代表更好，也可能只是因为模型销售覆盖太窄、触发的需求不足。

Tool use 也不是单调收益信号。selected LLM runs 的 tools/day 范围约 10.4-106.9。低工具调用的 Grok-4.3 / react 覆盖和销量偏低；但高工具调用的 runs 也不必然更好。差异更像是 evidence selection 和 action translation 的质量差异，而不是简单的工具调用次数差异。

### Why LLM Runs Differ from the Oracle-Style Heuristic

Non-LLM Heuristic 是手写 quality-based policy，用作 approximate oracle-style reference，不是 fair LLM baseline。它没有自然语言推理、上下文压缩、工具选择、JSON/action 格式、token budget 或每日候选 SKU 选择的不确定性；同时它显式编码了 supplier quality、补货节奏、shelf assortment 和批量采购策略。

差距不只是 survival。即使 DeepSeek-V4-Pro / plan_and_act 和 GPT-5.5 / react 都活到 180 天，它们的 final networth 仍分别低于 heuristic 121,389.51 和 107,159.43；best LLM networth 是 24,350.98，仍显著低于 heuristic 的 131,510.42。total sales 也类似：best selected LLM sales 是 164,417，低于 heuristic 的 267,998。

更直接的 operational gap 是质量控制。heuristic 的 return ratio 是 0.0201，expired ratio 是 0.0059；selected LLM runs 普遍高于这个水平。这和 paper 中的环境设定一致：supplier choice 会同时影响 procurement cost、quality、reviews、return pressure 和未来需求。LLM agent 即使能看到一部分 evidence，也需要持续把 supplier quality、price、inventory age、cash constraint 和 demand shocks 组合成稳定 policy；这正是 heuristic 手写策略的优势。

### Selected LLM Gap to Heuristic

Gap 定义：`Days/Networth/Sales Gap = heuristic - selected LLM`；`Return/Expired Ratio Over Oracle = selected LLM - heuristic`，正数表示高于 heuristic。

| Model | Selected | Days | Days Gap | Networth Gap | Sales Gap | Return Ratio Over Oracle | Expired Ratio Over Oracle | Tools/day |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| DeepSeek-V4-Pro | plan_and_act | 180 | 0.0000 | 121,389.51 | 103,581.00 | 0.0657 | 0.0086 | 62.3500 |
| GLM-5.1 | react | 60 | 120.0000 | 133,644.78 | 260,982.00 | 0.1146 | 0.0239 | 39.9833 |
| GPT-5.5 | react | 180 | 0.0000 | 107,159.43 | 131,593.00 | 0.0444 | 0.0055 | 106.8611 |
| Grok-4.3 | react | 58 | 122.0000 | 130,682.02 | 256,693.00 | 0.0827 | 0.0518 | 10.3621 |
| Kimi-K2.6 | react | 130 | 50.0000 | 130,718.22 | 181,784.00 | 0.1025 | 0.0352 | 45.7615 |
| MiniMax-M2.5 | plan_and_act | 73 | 107.0000 | 131,908.37 | 244,477.00 | 0.0876 | -0.0009 | 40.1918 |
| Qwen3.5-397B-A17B | reflection | 71 | 109.0000 | 130,327.24 | 232,376.00 | 0.0846 | 0.0596 | 49.7606 |

## Stage 3 Supplier Quality Diagnostic

这一段把四阶段报告中的 Stage 3 结论同步到主 `report.md`。核心问题不是模型完全不会查询 supplier 信息，而是 supplier 选择被低价信号系统性主导。

- Non-LLM Heuristic 的 trace-level reference 指标按当前校正口径记录为：`QDepth = 1.0000`，`Price Depth = 1.0000`，`ActionCorr = 0.8863`。这里的 `QDepth` 和 `Price Depth` 使用 heuristic policy 的 fully specified decision rule 口径，而不是 LLM trace 中的自然语言查询覆盖率。
- 全量 LLM runs 的 `QualityFirst%` 为 21.5%，`PriceFirst%` 为 55.6%；survival-best runs 的 `QualityFirst%` 为 24.1%，`PriceFirst%` 为 55.8%。
- 日志审计样本（每个 survival-best run 最多 30 条成功下单 line）显示，下单前 supplier price query 覆盖率为 99.6%，但 supplier return/rating 等 quality proxy query 覆盖率只有 61.4%。
- 在未选中 raw quality 最优 supplier 的 order lines 中，65.6% 仍然选择了最低价 supplier。这支持“信息呈现不足 + action conversion 不足”的解释：价格最显眼、最容易比较，而质量需要跨 supplier return/rating、review、历史 outcome 等 proxy 做组合推断。
- 新增 first-order 诊断进一步说明：SQL 历史数据本身明显偏向高质量 supplier，unit-weighted raw quality rank 为 1.534，QualityFirst 为 55.8%，PriceFirst 只有 7.7%；但全部 LLM runs 对每个 SKU 第一次成功下单时，mean quality rank 为 3.168，QualityFirst 只有 26.8%，PriceFirst 为 53.5%。Survival-best runs 也类似：first-order mean quality rank 为 3.133，QualityFirst 24.4%，PriceFirst 52.7%。这说明模型不是后期 drift 后才偏离质量结构，而是在第一次 procurement decision 时就没有稳定识别 supplier quality rank。
- 写作时应把 `QualityFirst%` 表述为 hidden/raw quality diagnostic，而不是模型当时完全可见信息下的公平正确率；更稳妥的 claim 是：当前 LLM agent 没有形成 `supplier candidate table -> quality-adjusted ranking -> place_order` 的稳定闭环。

## Files

- `run_metrics.csv`: per-run flat table。
- `run_metrics.json`: 包含 manifest、metric definitions 和 run 结果的完整 JSON。
- `best_framework_by_model.csv`: 每个模型的默认 selected run。
- `metric_definitions.md`: 独立指标说明文件。
- `selected_llm_action_diagnostics.csv`: selected LLM runs 的 action-level diagnostics。
- `deep_diagnostic_analysis.md`: 模型间差异和 oracle-style heuristic gap 的深度解释。
- `llm_agent_failure_analysis_report.md`: 面向论文立论的 V1/V2 failure-analysis report 和自我审视。
- `four_stage_measurement_framework.md`: 四阶段 operational pipeline 的 metric、diagnostic 和 intervention 框架。
- `supplier_quality_failure_analysis.md`: Stage 3 supplier quality failure 的日志审计报告。
- `supplier_quality_failure_cases.json`: Stage 3 supplier quality failure 的 machine-readable 诊断与案例。
- `supplier_rank_history_first_order_analysis.md`: SQL 历史 supplier rank 与各 run 每个 SKU 第一次下单 supplier rank 的对比诊断。
- `first_order_supplier_rank_by_run.csv` / `first_order_supplier_rank_lines.csv` / `historical_supplier_rank_by_sku.csv`: first-order 与历史 SQL supplier rank 的表格输出。
