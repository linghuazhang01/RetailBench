# M2 核心想法与实现逻辑精简版

日期：2026-05-23

## 1. 一句话总结

M2 的核心不是给 agent 一个复杂的 supplier scoring formula，而是在 `place_order` 这类会改变环境状态的 action 之前，加一个 **Evidence-to-Action Controller**：它强制把可观察 evidence 整理成 supplier candidate table，再根据明确规则判断这个 action 是否应该执行。

最关键原则：

```text
不要使用 hidden quality_score。
只使用 agent 当前能通过工具查到的信息。
先看 observable quality proxy，再看 profit/sales fallback，最后才用 price tie-break。
```

## 2. 为什么需要 M2

当前 LLM runs 的主要问题不是完全不查数据，而是 **查到的数据没有稳定转成正确 action**。

具体表现是：

- Stage 2 query depth 不低，说明模型并不是完全不会查工具。
- Stage 3 action correction 很低，说明 evidence-to-action conversion 出问题。
- Supplier selection 经常变成 cheapest supplier selection。
- `view_current_date_supplier_prices` 展示最显眼的是 `Supplier | SKU | Price`，而 supplier quality 相关 evidence 需要模型主动去查 returns、supplier-specific sales/profit 等 proxy。

因此，不应简单说“模型不会推理”。更准确的说法是：

```text
当前 agent framework 没有强制模型在 action 前显式比较 supplier quality proxy。
LLM 在自由工具调用中自然会偏向最显眼、最容易比较的 price signal。
```

M2 要补的就是这个 framework 缺口。

## 3. M2 应该做什么

M2 插在 LLM 和 mutation tools 之间，尤其是 `place_order` 之前。

原始流程是：

```text
LLM 查一些数据
-> LLM 自己决定 supplier / quantity
-> 直接 call place_order
```

M2 后的流程是：

```text
LLM proposed place_order
-> M2 自动构建 supplier candidate table
-> M2 检查 supplier 是否合理
-> M2 检查 quantity / funds / pending inventory 是否合理
-> 合理则执行；不合理则返回 revision card，不执行 action
```

这就是 `Evidence-to-Action Controller`。

## 4. M2 不能使用什么

M2 不能使用 hidden `quality_score`。

虽然环境内部会用 supplier quality 来模拟 merchandise quality、return 等后果，但这不是 agent 可见信息。为了实验公平，M2 决策时不能读取：

- `supplier_manager.get_quality_score`
- supplier quote payload 里的 hidden quality field
- 任何 raw oracle quality signal

`QualityFirst%` 仍然可以作为 post-hoc diagnostic，用来分析 M2 是否更接近环境真实质量结构。但它不能作为 M2 的输入。

## 5. M2 可以使用什么

M2 只使用当前工具可观察信息：

| 信息 | 工具 | 作用 |
| --- | --- | --- |
| supplier price | `view_current_date_supplier_prices` | 获得候选 supplier 和 unit price |
| supplier-SKU return rate | `view_supplier_returns_avg_rate` | 主要 observed quality proxy |
| supplier-specific sales/profit | `view_sales_profit_history(..., supplier_id=...)` | return evidence 稀疏时的 fallback |
| inventory / pending orders | `view_inventory` | 判断是否重复补货、是否 overbuy |
| funds | `view_funds_and_date` | 判断 affordability |
| current SKU price | `view_sku_prices`，可选 | 做 margin sanity check |

## 6. Supplier 决策逻辑

第一版不要做复杂加权公式。推荐使用简单、可解释、可审计的 lexicographic rule。

对每个 SKU：

```text
1. 如果有足够 supplier-SKU return evidence：
   选择 return_rate 最低的 supplier。

2. 如果 return evidence 太稀疏：
   看 supplier-specific realized profit / sales history。
   选择 profit_per_unit 或 realized performance 更好的 supplier。

3. 如果 return 和 profit evidence 都不足：
   才 fallback 到 lowest price。
```

这就是 **Observed-Quality-first**。

它不是说 price 不重要，而是说 price 只能作为：

- tie-breaker；
- evidence sparse 时的 fallback；
- funds/margin constraint 下的 feasibility signal。

price 不应该默认成为第一目标。

## 7. Candidate Table 应该长什么样

M2 对每个 SKU 生成一张 supplier candidate table：

| supplier_id | return_rate | return_support | supplier_units_sold | supplier_profit_per_unit | unit_price | return_rank | price_rank | policy_rank | evidence_confidence |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |

M2 不是只告诉模型“选 supplier_2”。它要把比较过程显式化：

```text
这个 supplier 为什么被推荐？
另一个 supplier 为什么被拒绝？
这个判断是基于 supported return evidence，还是只是在 sparse evidence 下 fallback 到 price？
```

这对论文也很重要，因为它让 failure case 和 improvement 都可以被审计。

## 8. Action Gate 逻辑

当 LLM 尝试 `place_order` 时，M2 检查：

1. chosen supplier 是否是 observed-quality best supplier；
2. 如果不是，是否有合理 deviation reason；
3. order quantity 是否明显 overbuy；
4. pending / incoming inventory 是否已经覆盖需求；
5. funds 是否足够；
6. 如果多个 SKU 的最佳 supplier 不一致，是否需要 split order。

M2 应该允许合理偏离，例如：

- return evidence 很稀疏；
- top suppliers 的 return rate 几乎相同；
- quality-best supplier 太贵，导致资金不可行；
- requested supplier 是低价且 evidence 没有明显更差。

M2 应该 block 明显错误，例如：

- chosen supplier 是 price-rank 1，但 supported return rank 很差；
- chosen supplier 没有 evidence，而另一个 supplier 有 supported lower return；
- quantity 明显超过最近需求；
- pending order 已经覆盖该 SKU，agent 又重复补货；
- 一个 multi-SKU order 被迫使用一个 supplier，但 per-SKU 最佳 suppliers 实际不同。

## 9. Multi-SKU split 是必须处理的

当前 `place_order(items, supplier_id)` 有一个重要限制：一个 order 只能有一个 `supplier_id`。

但 M2 的 supplier comparison 是 per SKU 的。可能出现：

```text
SKU A 最佳 supplier = supplier_1
SKU B 最佳 supplier = supplier_3
SKU C 最佳 supplier = supplier_1
```

这时 M2 不应该强行选一个 supplier。它应该 block 原始订单，并推荐 split actions：

```json
[
  {
    "name": "place_order",
    "arguments": {
      "supplier_id": "supplier_1",
      "items": [{"sku_id": "A", "quantity": 5}, {"sku_id": "C", "quantity": 2}]
    }
  },
  {
    "name": "place_order",
    "arguments": {
      "supplier_id": "supplier_3",
      "items": [{"sku_id": "B", "quantity": 4}]
    }
  }
]
```

这是 M2 实现里一个非常关键的工程点。

## 10. Quantity Gate 为什么也需要

只选对 supplier 不够。模型还可能因为 overbuy、重复补货、资金锁死而失败。

因此 M2 至少要做一个保守 quantity gate：

```text
avg_daily_sales = past_30d_total_units / observed_days
available_stock = current_quantity + incoming + waiting
target_qty = avg_daily_sales * target_stock_days - available_stock
max_reasonable_qty = avg_daily_sales * max_stock_days - available_stock
```

建议：

- target coverage 可以先设 7 天；
- max coverage 可以先设 21 天；
- 超过 max coverage 的 order block 或 shrink；
- 如果 pending/incoming 已经够了，就 block duplicate replenishment。

这不需要复杂优化，只需要防止明显坏的采购动作。

## 11. 第一版最小实现

我建议第一版只实现 `place_order` gate，不要同时做 pricing。

最小实现包括：

1. 新增 `module/evidence_to_action_controller.py`。
2. 新增 `config/e2a/retail_default.json`。
3. 在 `RetailEnvironment` 初始化 `self.e2a`。
4. 在 `place_order` 真正 mutation 前调用 `self.e2a.before_action(...)`。
5. M2 内部构建 supplier candidate table。
6. 按 observed return -> supplier-specific profit -> price 的顺序推荐 supplier。
7. 对明显不合理 action 返回 `e2a_action_needs_revision`，并且 `action_executed=false`。
8. 在 runner 中让 `e2a_action_needs_revision` 像 CAEC 一样触发 reconsideration。
9. 记录 `e2a_decisions.jsonl`，保留 candidate table 和 block reason。

第一版先不要实现 `modify_sku_price`，因为 supplier selection 是当前更强、更清楚的 failure signal。

## 12. 如何验证 M2 是否有效

最清楚的 ablation 是：

| Variant | CAEC | M2 | 目的 |
| --- | --- | --- | --- |
| B0 | off | off | 原始 baseline |
| B1 | gate | off | 只补 evidence |
| B2 | gate | gate | evidence + action conversion |

重点看这些 metrics：

- chosen supplier observed-return rank 是否下降；
- price-first rate 是否下降；
- supplier return evidence coverage 是否上升；
- M2 block 后 agent 是否 follow recommended action；
- return ratio 是否下降；
- survival days / final networth / total sales 是否不崩；
- quantity / avg daily sales 是否更合理。

Post-hoc 可以继续看 raw `QualityFirst%`，但论文里要说明它只是 diagnostic，不是 M2 的 action-time input。

## 13. 论文里的核心表述

可以这样写：

```text
RetailBench shows that tool access alone is insufficient for long-horizon business agents.
The main failure is not merely missing evidence, but missing evidence-to-action conversion.
M2 addresses this gap by inserting an observable controller before mutation tools:
it converts scattered evidence into candidate comparison, constraint validation,
and revision feedback.
```

中文逻辑是：

```text
RetailBench 说明，仅仅给 agent 工具访问权限还不够。
当前失败不只是 evidence missing，而是 evidence 没有稳定转成 action policy。
M2 在 mutation tools 前加入一个可观察 controller，
把分散 evidence 转成 candidate comparison、constraint validation 和 revision feedback。
```

## 14. 最核心结论

M2 的本质是：

```text
把“模型自由决策”改成“模型提出 action，controller 用可见 evidence 审计 action”。
```

它最应该解决的是 supplier selection 中的 price salience bias：

```text
没有 M2：模型容易看到 price，就选 cheapest supplier。
有 M2：模型必须先看到 supplier candidate table，
       并解释为什么不选 observed-quality best supplier。
```

这比单纯 prompt 更强，因为它改变了 action 前的结构；也比 oracle policy 更公平，因为它不读取 hidden quality，只使用 agent 可见 evidence。
