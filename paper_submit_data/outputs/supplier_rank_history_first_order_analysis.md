# Supplier Rank: History SQL vs First Model Orders

## 结论先行

这个诊断使用内部 raw supplier quality 作为 post-hoc oracle，回答两个问题：SQL 历史数据本身来自哪些 quality-rank supplier，以及 LLM runs 对每个 SKU 第一次下单时选到的 supplier rank。

- SQL 历史数据中，unit-weighted mean quality rank 为 1.534，unit-weighted QualityFirst 为 55.8%。
- 全部 LLM first-order-by-SKU 中，mean quality rank 为 3.168，QualityFirst 为 26.8%，PriceFirst 为 53.5%。
- Survival-best LLM first-order-by-SKU 中，mean quality rank 为 3.133，QualityFirst 为 24.4%，PriceFirst 为 52.7%。

读法：如果模型第一次为某个 SKU 补货时已经识别到了 supplier quality structure，`first_order_quality_rank_mean` 应接近 1，`first_order_quality_first_rate` 应明显高于 price-first baseline。

## 历史 SQL supplier rank

| Unit | Records | Units | Distinct SKUs | QRank Mean | QFirst | PriceFirst |
|---|---|---|---|---|---|---|
| history order lines | 8336 | 216309 | 96 | 1.519 | 57.1% | 7.8% |
| history unit-weighted | 8336 | 216309 | 96 | 1.534 | 55.8% | 7.7% |

这里的 SQL 历史数据来自 `product_lifecycle` 中 `order_id LIKE 'hist-%'` 的 merchandise rows。line-level 指每个 `(order_id, sku_id, supplier_id, order_date)` 作为一条历史订货 line；unit-weighted 则按该 line 产生的 merchandise 数量加权。

## 每个 survival-best run 的 first-order rank

| Model | Framework | First SKUs | QRank | PriceRank | QFirst | PriceFirst |
|---|---|---|---|---|---|---|
| DeepSeek-V4-Pro | plan_and_act | 54 | 2.037 | 2.352 | 42.6% | 18.5% |
| GLM-5.1 | react | 22 | 3.182 | 3.182 | 13.6% | 27.3% |
| GPT-5.5 | react | 71 | 3.042 | 1.761 | 26.8% | 53.5% |
| Grok-4.3 | react | 27 | 3.259 | 2.333 | 14.8% | 44.4% |
| Kimi-K2.6 | react | 50 | 4.280 | 1.060 | 6.0% | 94.0% |
| MiniMax-M2.5 | plan_and_act | 28 | 3.643 | 1.321 | 17.9% | 78.6% |
| Qwen3.5-397B-A17B | reflection | 63 | 2.968 | 2.095 | 31.7% | 49.2% |

## 机制解释

这组 first-order 统计比全量 order-line 统计更接近“模型第一次面对某个 SKU supplier choice 时是否做对”。如果 first-order rank 已经很差，说明问题不是后期 drift 才出现，而是在初次 procurement decision 时就没有建立 supplier candidate comparison。

这个结果直接支持 M2 的设计：在 `place_order` 前构建 candidate table，并把 raw-quality-best 判断作为 post-hoc diagnostic 或 gate oracle。如果实际机制允许用内部信息判断最佳 supplier，就可以直接用 `quality_rank=1` 作为 action revision 的触发条件；如果要保持 fair agent setting，则只能把它用于分析，不作为 agent-visible evidence。

## 输出文件

- `historical_supplier_rank_by_sku.csv`：每个 SKU 在历史 SQL 中的 supplier rank 汇总。
- `first_order_supplier_rank_by_run.csv`：每个 run 的 first-order-by-SKU supplier rank 汇总。
- `first_order_supplier_rank_lines.csv`：每个 run 每个 SKU 第一次下单的明细。
- `supplier_rank_history_first_order_analysis.json`：完整 machine-readable payload。

## 证据边界

本报告使用 hidden/raw quality 做 post-hoc oracle。它适合解释 benchmark 机制与 agent failure，但如果论文讨论的是模型当时可见信息下的公平决策，则必须明确 raw quality rank 不是 agent-visible signal。
