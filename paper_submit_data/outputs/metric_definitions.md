# Metric Definitions

Definitions follow `paper/latex_arr_revision/capter/experiment.tex` and `paper/latex_arr_revision/table/final_evaluation_summary.tex`.

## operation
- `run_days` (越高越好): episode 终止或分析窗口结束前的经营天数，越高越好。
- `final_networth` (越高越好): 最后一个分析日的 net worth，越高越好。

## sales
- `total_sales` (越高越好): 分析窗口内累计售出件数，由每日 sales_by_sku 汇总得到，越高越好。
- `avg_daily_sold_skus` (越高越好): 平均每天实际发生销售的不同 SKU 数量，越高表示销售覆盖更广。
- `return_ratio` (越低越好): 退货件数除以售出件数，越低越好。

## inventory
- `expired_ratio` (越低越好): 过期件数除以售出件数与过期件数之和，越低越好。
- `stockout_days` (越低越好): insufficient_skus 非空的天数，越低越好。
- `stockout_ratio` (越低越好): 缺货天数除以分析天数，越低越好。

## tool_use
- `avg_direct_tool_calls_per_day` (越高越好): 平均每天顶层工具调用次数，不包含 execute_code 内部触发的工具调用。
- `avg_all_tool_calls_per_day` (越高越好): 平均每天总工具调用次数，包含顶层工具调用与 execute_code 内部触发的工具调用。

## token_cost
- `avg_tokens_per_day` (越低越好): total_tokens 除以有 token usage 记录的天数。
- `avg_cost_usd_per_day` (提供 token 单价时越低越好): 可选的每日平均费用估计；未提供 token 单价时为空。
