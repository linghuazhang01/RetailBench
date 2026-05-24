# Supplier Quality Failure Analysis

## 结论先行

大部分 LLM run 没有选中 raw `quality_score` 最高的 supplier，不是单一原因。主要问题是 supplier quality 在普通工具展示层没有显式暴露，而模型实际日志又显示出明显的 price-first 行为；因此这是“信息呈现不足 + 模型/提示策略偏向低价”的叠加问题。

关键证据：
- 全部 LLM runs 的加权 Quality First Top-1 rate 是 21.5%，Price First Top-1 rate 是 55.6%。
- Survival-best runs 的 Quality First Top-1 rate 是 24.1%，Price First Top-1 rate 是 55.8%。
- 在每个 survival-best run 最多 30 条成功下单 line 的日志审计样本中，未选中最高质量 supplier 的 order lines 里，65.6% 仍然选择了最低价 supplier。
- 同一日志审计样本里，下单前 supplier price query 覆盖率为 99.6%，但 supplier return/rating 等 quality proxy query 覆盖率只有 61.4%。

## 数据层面：quality 信息是否被提供？

分三层看：

1. 普通工具展示层没有直接提供 raw quality。`view_current_date_supplier_prices` 的 formatted 表格只有 `Supplier | SKU | Price`，没有 `quality_score`、return rate 或 quality rank。
2. 原始 payload 层有部分隐藏信息。该工具内部 payload 保存 supplier entries；在 `execute_code` 中，tool proxy 会返回 `result` 字段，因此模型如果主动写代码检查 raw entry，有机会看到更完整结构。
3. 可观察 proxy 层存在。环境提供 `view_supplier_returns_avg_rate` 和 `view_sku_avg_ratings`，可以用来估计 supplier/SKU quality，但日志显示模型下单前很少系统调用这些 proxy。

所以不能说数据完全没有提供；更准确的说法是：benchmark 没有把 supplier quality 作为直接、可排名、可解释的 action precondition 呈现给 agent，模型需要主动发现并组合 proxy。

## 行为层面：模型是否偏向低价？

下面这张表中 rank-rate 来自完整 `four_stage_metrics.csv`；`QualityProxyBeforeOrder` 来自 log-context audit sample，每个 survival-best run 最多取 30 条成功下单 line。

| Model | Framework | Order lines | QualityFirst | PriceFirst | Avg QRank | Avg PriceRank | QualityProxyBeforeOrder |
|---|---|---|---|---|---|---|---|
| DeepSeek-V4-Pro | plan_and_act | 850 | 23.3% | 55.7% | 3.29 | 1.67 | 100.0% |
| GLM-5.1 | react | 47 | 31.9% | 17.0% | 2.55 | 3.23 | 87.5% |
| GPT-5.5 | react | 829 | 34.6% | 45.5% | 2.69 | 1.98 | 100.0% |
| Grok-4.3 | react | 71 | 14.1% | 54.9% | 3.37 | 2.00 | 0.0% |
| Kimi-K2.6 | react | 316 | 12.5% | 81.3% | 3.89 | 1.28 | 100.0% |
| MiniMax-M2.5 | plan_and_act | 178 | 14.0% | 59.0% | 3.46 | 1.95 | 43.3% |
| Qwen3.5-397B-A17B | reflection | 320 | 16.9% | 61.9% | 3.50 | 1.88 | 0.0% |

读法：如果模型真的在 quality-first 地选择 supplier，`QualityFirst` 应接近 100%，`Avg QRank` 应接近 1。实际多数 survival-best runs 的 `Avg QRank` 在 2.5 到 3.9 之间，同时 `PriceFirst` 往往显著高于 `QualityFirst`。

## 具体日志案例

| Model | Framework | Date | SKU | Chosen | QRank | PriceRank | BestQ | ChosenQ | BestQScore | PriceSeen | QualityProxySeen |
|---|---|---|---|---|---|---|---|---|---|---|---|
| MiniMax-M2.5 | plan_and_act | 1991-09-07 | 4400000004 | supplier_3 | 5 | 1 | supplier_1 | 1.8091 | 4.6812 | Y | N |
| MiniMax-M2.5 | plan_and_act | 1991-09-07 | 1862702345 | supplier_3 | 5 | 1 | supplier_1 | 1.7116 | 4.9566 | Y | N |
| GLM-5.1 | react | 1991-09-10 | 3700060511 | supplier_4 | 5 | 1 | supplier_2 | 1.6182 | 4.848 | Y | Y |
| GLM-5.1 | react | 1991-09-10 | 8248812345 | supplier_3 | 5 | 1 | supplier_2 | 1.7637 | 4.731 | Y | Y |
| Kimi-K2.6 | react | 1991-09-07 | 3828153081 | supplier_1 | 5 | 1 | supplier_2 | 2.0197 | 4.9911 | Y | Y |
| Kimi-K2.6 | react | 1991-09-07 | 3040000048 | supplier_1 | 5 | 1 | supplier_3 | 1.7867 | 4.9428 | Y | Y |
| Qwen3.5-397B-A17B | reflection | 1991-09-09 | 8066095605 | supplier_3 | 5 | 1 | supplier_4 | 1.6669 | 4.6861 | Y | N |
| Qwen3.5-397B-A17B | reflection | 1991-09-09 | 4400000004 | supplier_3 | 5 | 1 | supplier_1 | 1.7542 | 4.6778 | Y | N |
| Grok-4.3 | react | 1991-09-07 | 8066095605 | supplier_3 | 5 | 1 | supplier_4 | 1.4411 | 4.6715 | Y | N |
| Grok-4.3 | react | 1991-09-07 | 8000000280 | supplier_3 | 5 | 1 | supplier_4 | 1.8699 | 4.895 | Y | N |
| GPT-5.5 | react | 1991-09-07 | 3828153081 | supplier_1 | 5 | 1 | supplier_2 | 2.0197 | 4.9911 | Y | Y |
| GPT-5.5 | react | 1991-09-07 | 2500002561 | supplier_1 | 5 | 1 | supplier_5 | 2.1009 | 4.9093 | Y | Y |

这些案例的共同模式是：模型经常已经查过 supplier price，但没有查或没有利用 supplier-level quality proxy；最终选择在 price rank 上更靠前，而不是 raw quality rank 上靠前的 supplier。

## 机制解释

1. **工具接口造成 price salience**：普通 supplier quote 表只显示价格，模型自然把“supplier selection”解释成 cheapest supplier selection。
2. **prompt 示例强化了 cheapest heuristic**：`run_plan_and_act.py` 和 `run_step_reflection.py` 的 execute_code 示例显式用 `q['price'] < best['price']` 选择 supplier。这会把 agent 的默认策略锚定在低价，而不是 quality-adjusted cost。
3. **quality proxy 需要跨工具组合**：真正合理的 quality-first 策略需要把 supplier quote、supplier return rate、SKU reviews/ratings、历史销量和库存风险拼起来。多数 run 没有稳定完成这一步。
4. **action conversion 弱**：即使模型知道要补货，也常把“订哪个 supplier”降解为局部价格最小化，没有把 delayed returns / expiration / customer satisfaction 纳入 supplier objective。

## 对论文的写法建议

可以把这个 failure 写成一个 grounded diagnostic：LLM agent 不是完全看不到质量线索，而是没有形成稳定的信息获取和 action conversion loop。在 RetailBench 里，supplier quality 是 delayed, partially observable, and proxy-mediated；LLM 在 open-ended tool use 下倾向选择最显眼、最局部、最容易比较的 price signal。

可行改进策略：
- 工具改进：把 supplier quote 表扩展为 `price + return-rate proxy + rating proxy + delivery window + quality rank`，或新增 `rank_supplier_candidates(sku_id, objective)`。
- Prompt 改进：删除 cheapest 示例，要求每次 `place_order` 前输出 candidate table，并显式说明选择 supplier 的 trade-off。
- Policy 改进：引入 action validator，在下单前检查是否查询了 supplier return/rating proxy，并在未查询时触发补证据。
- Memory 改进：把已观察到的 supplier failure/return signal 写入 notes，避免每天重新局部决策。

## 证据边界

这里的 `QualityFirst` 使用 hidden/raw `quality_score` 作为诊断 oracle，不等价于模型当时完全可见的信息。因此它不应被写成 fairness-normalized action correctness；更适合用作“agent 是否接近环境真实质量结构”的 diagnostic。对模型可见信息下的合理性，仍应结合 supplier return/rating proxy coverage 一起解释。
