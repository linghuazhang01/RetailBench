# RetailBench Agent Mechanism Optimization Report

Date: 2026-05-23

## 1. 结论先行

当前数据最明确地说明了一件事：LLM agent 的主要问题不是“没有工具”，而是没有把工具返回的信息稳定转成 operating policy。四阶段分析里，Stage 2 的 query depth 已经不低，survival-best LLM runs 的平均 `QDepth` 约为 0.824，但 Stage 3 的平均 `ActionCorr` 只有约 0.233；supplier 选择也明显偏向低价，survival-best LLM runs 的 `QualityFirst%` 约 24.1%，`PriceFirst%` 约 55.8%。这说明 agent 不是完全看不到证据，而是缺少一个把 evidence 转为 supplier、quantity、price action 的中间控制层。

我认为最有价值的机制不是单纯 prompt 改写，而是 **Evidence-to-Action Controller**：一个插在 LLM 与 mutation tools 之间的 agent module。它负责生成候选 SKU、构造 supplier/price candidate table、检查证据是否覆盖、给出推荐 action，必要时阻止或要求 agent 重新决策。这个机制比纯 prompt 更稳，比端到端学习更容易实现，也直接对准当前最强的 failure signal。

## 2. 数据证据摘要

下面这些证据来自 `four_stage_analysis_report.md`、`supplier_quality_failure_analysis.md` 和 `four_stage_metrics.csv`。

| 观察 | 数据证据 | 机制含义 |
| --- | --- | --- |
| SKU 覆盖不足 | survival-best LLM `Acted/day` 平均约 3.64，而 Non-LLM heuristic 是 16.74；LLM `Missed high-demand rate` 平均约 0.660。 | agent 每天没有稳定的 SKU candidate generator，导致很多后续 action 根本不会发生。 |
| 查询不等于决策 | survival-best LLM `QDepth` 平均约 0.824，但 `ActionCorr` 平均只有约 0.233。 | 信息访问和 action conversion 之间缺少结构化决策层。 |
| supplier 选择偏低价 | 全量 LLM `QualityFirst% = 21.5%`，`PriceFirst% = 55.6%`；survival-best `QualityFirst% = 24.1%`，`PriceFirst% = 55.8%`。 | supplier selection 被 price salience 主导，quality proxy 没有稳定进入 objective。 |
| quality 证据没有稳定转化 | log audit 中 supplier price query 覆盖率 99.6%，quality proxy query 覆盖率 61.4%；未选最高质量 supplier 的 order lines 中 65.6% 仍选最低价。 | 即使部分质量证据存在，agent 也没有形成 candidate comparison table。 |
| temporal follow-up 不足 | survival-best LLM `Follow action` 平均约 0.379，Non-LLM heuristic 为 0.9449。 | 需要 action ledger 和 follow-up scheduler，而不是每天重新从零思考。 |

## 3. Failure Mechanism

### 3.1 Stage 1: 缺少 daily SKU candidate generator

RetailBench 不是单 SKU 决策。每天先决定关注哪些 SKU，本身就是一个高难度 action。当前 LLM 往往用自然语言上下文里的局部显著信息选 SKU，导致两个问题：

- 覆盖面太小：弱 run 每天 action SKU 很少，销售 SKU/day 也低；
- focus 漂移：今天管了一个 SKU，明天可能完全转向别的 SKU。

这会让后续工具调用和 action 都变成局部优化。没有进入 daily candidate set 的 SKU，即便有 stockout、high demand、review drift，也不会被补货或调价。

### 3.2 Stage 2: 查询缺少 action-specific precondition

很多 run 会查数据，但查的数据不一定和即将执行的 action 精确对齐。`place_order` 至少需要库存、近期销量、supplier quote、supplier quality/return proxy、cash、pending order；`modify_sku_price` 至少需要当前价格、成本、销量曲线、库存压力、利润信号。当前 agent 的查询更像 open-ended exploration，而不是 action precondition checking。

已有 CAEC 已经是正确方向，但它的边界还不够：它更多是在 attempted action 后补齐所选 action 的 missing evidence，而不是在 action 前强制构造“所有候选 supplier/price 的比较表”。

### 3.3 Stage 3: 最大瓶颈是 evidence-to-action conversion

Stage 3 是当前最强 failure signal。尤其 supplier selection 的问题非常清楚：`view_current_date_supplier_prices` 的 formatted view 只突出 `Supplier | SKU | Price`；真实 `quality_score` 不会作为可见决策字段提供，agent 只能主动查询并组合 `view_supplier_returns_avg_rate`、`view_sales_profit_history(..., supplier_id=...)`、`view_sku_avg_ratings` 这类 proxy。模型如果没有结构化候选表，自然会把 supplier selection 简化成 cheapest supplier selection。

这里不应简单说“模型不会推理”。更准确的是：当前 agent framework 没有把 supplier quality 作为 action 前必须显式比较的变量。LLM 在自由工具调用中倾向使用最显眼、最局部、最容易比较的 price signal。

### 3.4 Stage 4: delayed signals 没有进入稳定状态

RetailBench 的很多反馈是 delayed：returns、reviews、expiration、stockout、supplier delivery 都会滞后出现。当前 LLM agent 没有稳定维护一个 action ledger，把前几天的采购、调价、stockout、return 汇总成今天的 follow-up obligations。FCR 提醒已经提供了一部分“focus continuity”，但它还不是一个真正的 scheduler，也没有把 supplier quality memory 和 SKU state memory 合并起来。

## 4. 可做的机制优化

### M1. Daily SKU Watchlist Generator

**目标**：解决 Stage 1 覆盖问题。

每一天开始时，先由 module 生成一个 bounded watchlist，例如 top-20 SKU。打分可以由以下因素组成：

```text
sku_priority =
  stockout_risk
  + recent_sales_velocity
  + gross_margin_potential
  + inventory_age_or_expiration_pressure
  + pending_order_gap
  + review_or_return_risk
  + news_exposure
```

输出给 LLM 的不是全量 SKU，而是分桶后的候选：

- `urgent_restock`: 缺货、高需求、pending 不足；
- `price_review`: 价格偏离估计最优区间；
- `quality_risk`: return/review 异常；
- `follow_up`: 最近 action 后需要复查；
- `explore`: 少量新机会 SKU。

**实现位置**：

- 新增 `module/sku_watchlist.py`；
- 在 `run_plan_and_act.py` 每天 plan phase 前调用；
- 将 watchlist 写入 prompt，并写入 `tool_calls.jsonl` 或单独 `watchlist.jsonl` 便于评估。

**预期改善指标**：

- `s1_avg_acted_skus_per_day` 上升；
- `s1_missed_high_demand_rate` 下降；
- total sales 和 sold SKU/day 上升。

**风险**：

- 如果 watchlist 太强，会把 LLM 变成执行器而不是决策者；
- 需要保留少量 exploration budget，避免完全锁死到历史高销量 SKU。

### M2. Evidence-to-Action Controller

**目标**：解决 Stage 2/3 的核心问题，即“查到了但不会转 action”。

这是我认为最有价值的机制。它应当作为 LLM 和 mutation tools 之间的控制层，覆盖 `place_order` 和 `modify_sku_price`。

#### M2.1 Procurement Candidate Comparator

对每个 `place_order` 目标 SKU，controller 自动构造 candidate table：

| 字段 | 来源 |
| --- | --- |
| supplier_id | `view_current_date_supplier_prices` |
| unit_price | supplier quote |
| supplier_return_rate | `view_supplier_returns_avg_rate`，按 supplier + SKU + date range 查询 |
| supplier_sku_sales_profit | `view_sales_profit_history(..., supplier_id=...)`，看该 supplier 供货后的实际销量和利润 |
| sku_rating | `view_sku_avg_ratings`，作为 SKU 层面的质量/需求风险，不直接区分 supplier |
| delivery_range | supplier payload / order outcome |
| recent_sales_velocity | `view_sales_profit_history` |
| current_inventory / pending | `view_inventory` |
| cash affordability | `view_funds_and_date` |

这里不建议第一版就做复杂加权，也不能依赖 hidden `quality_score`。更好的最小机制是 **Observed-Quality-first**：

```text
primary objective:
  choose the supplier with the best observed quality signal:
    1. lowest supplier-SKU return rate when there is enough support
    2. otherwise, best supplier-specific realized profit / sales history
    3. otherwise, fall back to price as a tie-breaker

tie-breakers / safety constraints:
  1. if observed quality signals are tied or too sparse, choose lower unit_price
  2. reject suppliers whose unit_price would make the expected margin unsafe
  3. reject orders that violate cash, inventory, pending-order, or demand-scale constraints
  4. optionally flag very slow delivery, but do not let delivery dominate observed quality in v1
```

也就是说，第一版不要让模型自己学习一个复杂 utility function，而是强制它先做一个可读的 candidate table：

| supplier_id | return_rate | return_support | supplier_units_sold | supplier_net_profit | unit_price | return_rank | price_rank | evidence_confidence | selected_reason |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- | --- |

然后要求模型默认选择 `return_rank = 1` 且 evidence support 足够的 supplier。只有在 return evidence 太稀疏、return rate 几乎相同、价格差极大、资金不足或明显交期风险时，才允许偏离 observed-quality best supplier。

这个版本更简单，也更适合作为 fair agent mechanism：它只依赖当前工具可观察信息。如果 Observed-Quality-first 能降低 `PriceFirst%`、提高 return/rating proxy coverage、并降低后续 return ratio，就能说明问题确实是 supplier quality signal 没有进入 action conversion。

证据边界要说清楚：`QualityFirst%` 仍然可以作为 post-hoc diagnostic，但 controller 本身不能读取 raw `quality_score`。主实验应报告 observed metrics，例如 supplier return/rating evidence coverage、chosen supplier return rank、return ratio、net worth 和 sales。

#### M2.2 Action Gate

当 LLM 调用 `place_order` 时，controller 检查：

- 是否已经有 candidate table；
- chosen supplier 是否是 observed-quality best supplier，例如 lowest supported supplier-SKU return rate；
- 如果不是 observed-quality best，是否满足允许偏离条件：return evidence 太稀疏、return rate 接近、价格差极大、资金不足、或交期明显更优；
- order quantity 是否在 demand-based range；
- cash 和 inventory capacity 是否允许；
- 是否存在 pending order 重复补货。

如果不满足，controller 返回一个 non-mutating card：

```json
{
  "status": "action_needs_revision",
  "action_executed": false,
  "reason": "chosen supplier is price-rank 1 but return-rank 5",
  "candidate_table": [...],
  "recommended_action": {...}
}
```

这和现有 CAEC 很接近，但关键差异是：CAEC 当前偏 evidence completion；M2 是 candidate comparison + policy validation。它不仅补证据，还把证据转成排序结果。

#### M2.3 Pricing Controller

对 `modify_sku_price`，controller 给出 price band：

```text
candidate_price_band =
  [max(cost * min_margin, optimal_price * 0.85),
   optimal_price * 1.15]
```

其中 `optimal_price` 可以沿用当前 Stage 3 analysis 中的历史销量/成本估计方法。LLM 如果要设很极端的价格，必须给出库存清仓、新闻冲击或现金流理由。

**实现位置**：

- 新增 `module/evidence_to_action_controller.py`；
- 复用 `module/context_aware_evidence_completion.py` 的 target extraction 和 visible tool response metadata；
- 在 `RetailEnvironment.place_order` 和 `RetailEnvironment.modify_sku_price` 内，CAEC 之前或之后调用 controller；
- 或者先以 runner-level wrapper 实现，在执行 mutation tool 前拦截 tool call。

**预期改善指标**：

- `s3_supplier_quality_first_rate` 上升；
- `s3_supplier_price_first_rate` 下降；
- `s3_supplier_quality_rank_mean` 接近 1；
- `s3_modify_price_distance_pct_mean` 下降；
- return ratio 下降；
- `s3_action_correction` 上升。

**为什么这是最高价值机制**：

1. 它直接命中最强数据瓶颈：Stage 3 action conversion；
2. 它利用现有工具和 CAEC 架构，不需要重新训练模型；
3. 它产生可审计日志，能支持论文里的机制分析；
4. 它可以和任意 LLM/model/framework 组合使用；
5. 它比 prompt scaffold 更稳，因为排序和 gate 不依赖模型自觉执行。

### M3. Supplier-SKU Quality Memory

**目标**：解决 supplier quality 的 delayed feedback。

维护一个 ledger：

```json
{
  "supplier_id": "supplier_3",
  "sku_id": "4400000004",
  "orders": 12,
  "units_ordered": 230,
  "avg_delivery_days": 4.2,
  "return_rate_30d": 0.18,
  "rating_trend_60d": -0.4,
  "last_bad_event": "high_return_after_order",
  "quality_proxy": 0.31
}
```

每天结束后更新；每天采购前读取相关 supplier-SKU memory。这个模块不应替代当天 quote，因为 supplier price 和 delivery 可能变；它提供的是 prior。

**实现位置**：

- 新增 `module/supplier_quality_memory.py`；
- 从 `tool_calls.jsonl`、`records.db`、reviews/returns 工具结果中更新；
- 通过 prompt context 或新 read tool `view_supplier_quality_memory(sku_ids)` 暴露给 agent。

**预期改善指标**：

- long-horizon `return_ratio` 下降；
- `QualityFirst%` 和 quality proxy rank 改善；
- Stage 4 follow-up 和 repeat no-attn 改善。

**风险**：

- 50-day 实验可能看不全长期收益；
- 如果 memory 使用 hidden raw quality，会破坏 fair setting，必须只用 observed returns/reviews/order outcomes。

### M4. Follow-Up Scheduler

**目标**：把 Stage 4 从“软提醒”升级成真正的 obligations。

每个 mutation action 生成 follow-up tasks：

| Action | Follow-up |
| --- | --- |
| `place_order` | 到货日前后检查 inventory、shelf、sales、returns |
| `modify_sku_price` | 1/3/7 天后检查销量和 profit 变化 |
| stockout event | 下一天检查是否补货或替换 shelf |
| high return event | 检查 supplier、rating、是否停止采购 |
| expiration event | 检查价格/采购量/货架策略 |

**实现位置**：

- 扩展 `agents/focus_coherence_reflection.py`，从 action ledger 变成 task scheduler；
- 新增 `followup_tasks.jsonl`；
- 每天 prompt 前输出 top-N due tasks；
- action 后自动 mark resolved 或 carry over。

**预期改善指标**：

- `s4_followup_action_rate_7d` 上升；
- `s4_repeated_error_without_intervention_rate` 下降；
- stockout/return/expiration 的 unresolved rate 下降。

### M5. Hierarchical Retail Agent

**目标**：把单一 LLM loop 拆成固定角色，降低 prompt drift。

推荐结构：

```text
Daily Manager
  -> SKU Watchlist Generator
  -> Evidence Collector
  -> Procurement Controller
  -> Pricing Controller
  -> Shelf Controller
  -> Follow-Up Scheduler
```

LLM 的职责从“自由决定一切”变成：

- 审阅 module 生成的候选；
- 在边界情况下做 trade-off；
- 给出解释；
- 处理 news/review 等非结构化信息。

这适合论文中的 framework contribution，但实现成本高于 M2。

### M6. Heuristic-Trajectory Distillation

**目标**：利用 non-LLM heuristic 的 headroom 生成 policy demonstrations。

可以把 heuristic trajectories 转成：

- retrieval examples；
- supervised fine-tuning 数据；
- action preference pairs；
- controller threshold calibration data。

但这不是我建议第一优先做的机制，因为它更像学习方法，实验成本高，并且容易被 reviewer 质疑是否只是在复制 heuristic。更适合作为后续增强或 appendix。

## 5. 机制优先级排序

| Rank | 机制 | 主要解决 stage | 实现成本 | 预期收益 | 推荐度 |
| --- | --- | --- | --- | --- | --- |
| 1 | Evidence-to-Action Controller | Stage 2/3 | 中 | 高 | 最高 |
| 2 | Daily SKU Watchlist Generator | Stage 1 | 中 | 高 | 高 |
| 3 | Supplier-SKU Quality Memory | Stage 3/4 | 中高 | 中高 | 高，但需长 horizon |
| 4 | Follow-Up Scheduler | Stage 4 | 中 | 中 | 高，适合作为 M2 后续 |
| 5 | Hierarchical Retail Agent | 全阶段 | 高 | 高 | 适合论文 framework 版 |
| 6 | Prompt Scaffold | Stage 3 | 低 | 中低 | 可做 quick ablation，不应作为最终主贡献 |
| 7 | Heuristic Distillation | 全阶段 | 高 | 不确定 | 后续探索 |

## 6. 我认为最优价值的机制

### 推荐：Evidence-to-Action Controller

它的核心不是替 LLM 做所有决策，而是把 RetailBench 中最容易出错的 action conversion 部分结构化：

```text
LLM intention
  -> required evidence check
  -> candidate table construction
  -> supplier/quantity/price scoring
  -> action gate
  -> mutation tool execution or revision card
```

选择它作为最优价值机制的原因：

1. 数据上，Stage 3 是最强瓶颈；
2. supplier failure 有非常明确的机制证据：price evidence 几乎都在，quality proxy 不稳定，最终 action 仍偏 cheapest；
3. 现有代码已经有 CAEC 和 tool response metadata，可以复用；
4. 它不需要改环境动态，也不需要训练；
5. 它天然可记录 candidate table、rejection reason、recommended action，能成为论文中很强的 qualitative evidence；
6. 它可以逐步上线：先 procurement，再 pricing，再 shelf/follow-up。

## 7. 具体实现方案

### 7.1 新增模块

建议新增：

```text
module/evidence_to_action_controller.py
```

核心类：

```python
@dataclass(frozen=True)
class CandidateScore:
    sku_id: str
    supplier_id: str
    return_rate: float | None
    return_rank: int | None
    return_support: int
    supplier_units_sold: int
    supplier_net_profit: float | None
    unit_price: float
    price_rank: int
    affordable: bool
    expected_margin: float | None
    allow_deviation: bool
    reasons: list[str]


class EvidenceToActionController:
    def before_place_order(self, args: dict[str, Any]) -> dict[str, Any] | None:
        ...

    def before_modify_price(self, args: dict[str, Any]) -> dict[str, Any] | None:
        ...
```

返回 `None` 表示允许执行；返回 dict 表示阻止执行并给 agent 一个 revision card。

### 7.2 Procurement gate 逻辑

伪代码：

```python
def before_place_order(args):
    items = args["items"]
    chosen_supplier = args["supplier_id"]
    sku_ids = [item["sku_id"] for item in items]

    evidence = collect_or_reuse_evidence(
        inventory=True,
        sales_30d=True,
        supplier_quotes=True,
        supplier_returns_30d=True,
        supplier_specific_sales_profit_30d=True,
        funds=True,
    )

    candidate_table = []
    for sku_id in sku_ids:
        for supplier in quotes[sku_id]:
            row = build_observed_quality_candidate(
                sku_id=sku_id,
                supplier_id=supplier.id,
                price=supplier.price,
                return_rate=returns[supplier.id][sku_id].return_rate,
                return_support=returns[supplier.id][sku_id].denominator_count,
                supplier_units_sold=supplier_sales[supplier.id][sku_id].total_units,
                supplier_net_profit=supplier_sales[supplier.id][sku_id].net_profit,
                velocity=sales[sku_id].avg_units,
                inventory=inventory[sku_id],
                funds=funds,
            )
            candidate_table.append(row)

    best_observed = rank_by_observed_quality(candidate_table)
    chosen = find_chosen_candidate(candidate_table, chosen_supplier)
    if chosen.return_rank and chosen.return_rank > 1 and not chosen.allow_deviation:
        return revision_card(
            reason="chosen supplier is not the observed-quality best supplier",
            candidate_table=candidate_table,
            recommended_supplier=best_observed.supplier_id,
        )

    if quantity_outside_demand_range(items, sales, inventory, pending):
        return revision_card(candidate_table, recommended_quantity)

    return None
```

关键点：如果 evidence 缺失，controller 可以先返回 evidence card；如果 evidence 已够但 action 不合理，返回 revision card。这比 CAEC 只补证据更进一步。

### 7.3 Pricing gate 逻辑

```python
def before_modify_price(args):
    sku_id = args["sku_id"]
    new_price = args["new_price"]

    cost = view_sku_inventory_cost([sku_id])
    current_price = view_sku_prices([sku_id])
    sales = view_sales_profit_history([sku_id], last_30d)
    inventory = view_inventory()

    optimal = estimate_optimal_price(sales, cost, inventory_pressure)
    lower = max(cost * min_margin, optimal * 0.85)
    upper = optimal * 1.15

    if new_price < lower or new_price > upper:
        return revision_card(
            reason="price outside evidence-based band",
            optimal_price=optimal,
            allowed_band=[lower, upper],
        )
    return None
```

### 7.4 与当前代码的接入点

有两个实现路径。

**路径 A：接到环境 mutation tool 内部**

- 在 `RetailEnvironment.place_order` 中，CAEC 前后调用 controller；
- 在 `RetailEnvironment.modify_sku_price` 中调用 controller；
- 优点：所有 agent framework 都自动受益；
- 缺点：更像 environment-side controller，需要小心论文里说明它是 agent module，不是 benchmark rule。

**路径 B：接到 runner 的 tool execution loop**

- 在 `run_plan_and_act.py` / `run_react.py` 执行 tool call 前拦截；
- 优点：更清楚是 agent framework module；
- 缺点：每个 runner 都要接入，维护成本更高。

我建议先走路径 B 做实验，论文里也更容易解释为 agent framework；等机制稳定后再抽象成 shared wrapper。

### 7.5 日志与评估

controller 必须输出独立日志：

```text
controller_decisions.jsonl
```

每条记录包含：

- date；
- proposed action；
- evidence coverage；
- candidate table；
- selected candidate rank；
- decision: allow / block / revise；
- recommended action；
- reason codes。

这样可以新增 evaluation：

- controller block rate；
- accepted action observed-return rank；
- LLM override rate；
- recommended-vs-executed delta；
- blocked action counterfactual risk。

## 8. 实验建议

第一轮不要直接做大而全 hierarchical agent。建议按最小可解释 ablation：

| Experiment | Variant | Purpose |
| --- | --- | --- |
| E0 | baseline DeepSeek-V4-Pro plan_and_act hard_v2 first 50 days | 已有 baseline |
| E1 | prompt-only quality scaffold | 测试 cheapest prompt anchor 是否关键 |
| E2 | Observed-quality Controller for `place_order` only | 测试基于 return/profit 可见证据的 candidate gate 是否改善 supplier choice |
| E3 | Controller + pricing band | 测试 supplier 与 price action conversion 是否共同改善 |
| E4 | Controller + follow-up scheduler | 测试 delayed signal 闭环 |

主要指标：

- `s3_supplier_quality_first_rate` 上升；
- `s3_supplier_price_first_rate` 下降；
- `s3_supplier_quality_rank_mean` 下降；
- chosen supplier observed-return rank 下降；
- supplier return/rating evidence coverage 上升；
- `s3_modify_price_distance_pct_mean` 下降；
- `s3_action_correction` 上升；
- return ratio 不上升；
- final net worth / total sales 不崩；
- Stage 1/4 指标作为副指标观察。

## 9. 论文贡献角度

这套机制可以被写成一个更一般的观点：

> Long-horizon tool-use agents fail when evidence access is not converted into durable operating policy. RetailBench exposes this gap through SKU coverage, supplier-quality selection, price control, and delayed follow-up. An evidence-to-action controller improves the interface between LLM reasoning and state-changing business actions by forcing candidate comparison, evidence coverage, and constraint validation before mutations.

这个 framing 比“我们调了 prompt”更有论文价值。它也和数据报告对齐：当前强证据不是“模型不聪明”，而是“agent framework 缺少 policy formation layer”。

## 10. 当前最小可落地版本

如果只实现一个版本，我建议：

1. 先只管 `place_order`；
2. 不使用 `quality_score`，只使用当前可见工具：`view_supplier_returns_avg_rate`、`view_sales_profit_history(..., supplier_id=...)`、`view_current_date_supplier_prices`、`view_inventory`、`view_funds_and_date`；
3. controller 输出 supplier candidate table，至少包含 `return_rate / return_support / supplier_units_sold / supplier_net_profit / unit_price / return_rank / price_rank / affordable`；
4. 如果 chosen supplier 不是 supported return-rate 最优 supplier，默认阻止 action，除非 return evidence 太稀疏、return rate 几乎相同、价格或资金约束给出明确理由；
5. quantity gate 只做保守检查：避免明显重复补货、明显超出 30-day demand、明显资金不足；
6. 日志完整保留 candidate table、chosen rank、recommended supplier 和 block reason。

这版能直接回答最关键的问题：LLM 是否因为缺少 structured observed-quality comparison 而偏向 cheapest supplier。如果有效，下一步再加入 pricing gate 和 follow-up scheduler。

## 11. 证据边界

- 当前结论是 descriptive，不是统计显著性证明；
- `QualityFirst%` 使用 raw supplier quality 作为 diagnostic oracle，不能直接写成模型当时完全可见信息下的 fairness-normalized correctness；
- controller 不读取 raw `quality_score`；它只使用 return/rating/supplier-specific sales-profit 等可见 proxy。`QualityFirst%` 只能作为 post-hoc diagnostic，主 claim 应落在 observed-return rank、return ratio 和 final outcomes 上；
- 单 run 实验只能支持 mechanism plausibility，不能支持跨模型泛化；
- 如果要写成主贡献，至少需要 E1/E2/E3 的 ablation，而不只是一次 DeepSeek 50-day run。
