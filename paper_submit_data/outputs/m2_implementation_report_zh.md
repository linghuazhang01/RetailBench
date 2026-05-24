# M2 Evidence-to-Action Controller 实现报告

日期：2026-05-23

状态：实现设计，尚未执行实验

## 0. 执行摘要

M2 应该被实现成一个位于 LLM 和状态修改工具之间的 **agent-side mutation controller**。第一版应该优先覆盖 `place_order`，因为 Stage 3 的 supplier selection 是目前最清楚的 failure signal：当前 runs 经常会查询有用 evidence，但最终 action 仍然会被转成低价 supplier 选择。

核心设计选择是：**不要暴露或使用 hidden `quality_score`**。M2 只能使用当前 agent 可观察到的 evidence：

- `view_current_date_supplier_prices`
- `view_supplier_returns_avg_rate`
- `view_sales_profit_history(..., supplier_id=...)`
- `view_inventory`
- `view_funds_and_date`
- 可选使用 `view_sku_prices` 做 margin sanity check

这个机制应该是 **Observed-Quality-first**，而不是复杂的加权分数。controller 为每个要订货的 SKU 构建 supplier candidate table，基于可观察 quality proxies 对 supplier 排序，检查 quantity 和 cash constraints，然后决定允许订单执行，或者返回一个 non-mutating revision card，并给出推荐 action。

我建议分三阶段实现 M2：

1. **M2-v1：Procurement Candidate Comparator + Action Gate**，覆盖 `place_order`。
2. **M2-v1.1：Multi-SKU split recommendation**，因为当前 order API 对所有 SKUs 只接受一个 `supplier_id`。
3. **M2-v2：Pricing Controller**，覆盖 `modify_sku_price`。这一步应放在 v1 证明 action conversion 能改善 supplier decision 之后。

最小但有价值的版本是 v1。它已经足够测试论文核心 claim：LLM agents 表现不好，不是因为 evidence 不可用，而是因为 agent framework 缺少一个结构化的 evidence-to-action conversion layer。

## 1. M2 要解决的问题

当前诊断是：

- Stage 2 evidence access 不是主要瓶颈。
- Stage 3 action conversion 是主要瓶颈。
- Supplier choice 系统性地被最显眼的局部信号吸引，也就是 unit price。
- Quality 信息要么没有被查询，要么被查询后没有被转换成 supplier ranking。

M2 不应该抽象地尝试让 LLM “变聪明”。它应该把 agent workflow 从：

```text
LLM sees scattered evidence -> LLM directly calls place_order
```

转成：

```text
LLM proposes place_order
-> controller builds candidate table
-> controller validates supplier, quantity, and affordability
-> action executes only if it passes
```

这是一个 framework intervention，不是 hidden oracle。

## 2. 现有系统约束

### 2.1 `place_order` 当前每个订单只能有一个 supplier

当前签名：

```python
place_order(items: List[Dict[str, Any]], supplier_id: str)
```

这意味着一次调用可以订多个 SKU，但所有 order lines 都必须使用同一个 supplier。M2 的自然比较粒度是 per SKU，所以 controller 必须显式处理这个 mismatch。

如果不同 SKUs 的最佳 observed supplier 不一致，M2 不应该强行选择一个折中的单一 supplier。它应该返回一个 revision card，推荐把订单拆成多个 supplier-specific orders：

```json
{
  "status": "e2a_action_needs_revision",
  "action_executed": false,
  "reason": "best observed suppliers differ across order lines",
  "recommended_actions": [
    {"name": "place_order", "arguments": {"supplier_id": "supplier_1", "items": [...] }},
    {"name": "place_order", "arguments": {"supplier_id": "supplier_3", "items": [...] }}
  ]
}
```

这个细节很重要。如果没有 split recommendation，M2 可能会意外奖励一个“平均上可接受、但对单个 SKU 很差”的 supplier。

### 2.2 CAEC 已经能补 evidence，但不会对 candidates 排序

当前 CAEC 会检查 required evidence 是否已经对 agent 可见；如果不可见，它会返回一个 non-mutating evidence card。这个机制有用，但不够。CAEC 回答的是：

```text
Has the agent seen required evidence?
```

M2 必须回答的是：

```text
Given the evidence, is this mutation action a good action?
```

所以实现上可以复用 CAEC 的 pattern，但概念上不要把 M2 合并进 CAEC。CAEC 是 evidence completion。M2 是 policy validation。

### 2.3 现有 `place_order` 内部会读取 hidden quality 来做仿真

环境当前在创建 merchandise 和 order results 时，会在内部使用 supplier quality。M2 决策时不能使用这个字段。

实现规则：

- M2 只能调用 public read tools。
- 解析 supplier quote payload 时，M2 应该 whitelist `supplier_id`、`sku_id`、`price`。
- M2 不能调用 `supplier_manager.get_quality_score`。
- M2 decision logs 不能包含 `quality_score`。

这对论文公平性很重要。`QualityFirst%` 可以继续作为 post-hoc diagnostic，但不能作为 action-time input。

## 3. 推荐运行时架构

### 3.1 新增模块

新增：

```text
module/evidence_to_action_controller.py
config/e2a/retail_default.json
```

建议的类结构：

```python
@dataclass(frozen=True)
class E2ATargets:
    action_name: str
    items: list[OrderLine]
    supplier_id: str | None

@dataclass(frozen=True)
class SupplierCandidate:
    sku_id: str
    supplier_id: str
    unit_price: float
    return_rate: float | None
    return_support: int
    supplier_units_sold: int
    supplier_net_profit: float
    supplier_profit_per_unit: float | None
    return_rank: int | None
    price_rank: int
    policy_rank: int
    evidence_tier: str
    evidence_confidence: str
    reasons: list[str]

@dataclass(frozen=True)
class LineDecision:
    sku_id: str
    requested_quantity: int
    requested_supplier_id: str
    recommended_supplier_id: str
    allowed: bool
    block_reasons: list[str]
    deviation_reasons: list[str]
    candidate_table: list[SupplierCandidate]

@dataclass(frozen=True)
class E2ADecision:
    decision_id: str
    status: str
    action_executed: bool
    action_name: str
    attempted_action: dict[str, Any]
    line_decisions: list[LineDecision]
    recommended_actions: list[dict[str, Any]]
    formatted: str
```

### 3.2 Environment wiring

在 `RetailEnvironment.__init__` 中：

```python
self.e2a = EvidenceToActionController(self, config)
```

在 `place_order` 中，安全的执行顺序应该变成：

```text
1. Normalize and validate items/supplier.
2. Validate today's quote exists for requested supplier and SKU.
3. Compute requested total cost, but do not mutate state.
4. Let CAEC run first if enabled and missing visible evidence.
5. Let M2 run before mutation and before the final insufficient-funds return.
6. If M2 returns a revision card, return it with action_executed=false.
7. If M2 approves, execute existing place_order mutation.
```

为什么 M2 应该在最终 insufficient-funds return 之前运行：

- 如果 requested supplier 不可负担，但另一个 observed-quality 可接受的 supplier 更便宜，M2 可以推荐 revised order。
- 如果 requested quantity 太大，M2 可以推荐更小的 quantity。
- 只返回 `"Insufficient funds"` 会丢掉把 evidence 转成 feasible action 的机会。

### 3.3 Runner wiring

当前 runners 会特殊处理 CAEC status。M2 需要同样的 control flow。

新增一个通用 intervention detector：

```python
def is_intervention_status(status: str | None) -> bool:
    return bool(
        status
        and (
            status.startswith("caec_context_completed_action_not_executed")
            or status.startswith("e2a_action_needs_revision")
        )
    )
```

然后更新 `run_react.py`、`run_plan_and_act.py`、`run_reflection.py`、`run_step_reflection.py`：

- 当 M2 block action 时，suppress assistant message；
- blocked mutation 之后立即 break，避免同一个 assistant turn 里的后续 tool calls 继续执行；
- 把 M2 card 和 reconsideration prompt 返回给 LLM；
- 保持 `action_executed=false`，这样 FCR 不会把 blocked actions 记录成 executed actions。

这一步不是可选项。如果 M2 block action 后没有停止当前 tool-call batch，agent 可能会用 stale reasoning 继续执行同一条消息里后面的 mutation calls。

## 4. M2-v1 Procurement Comparator

### 4.1 Evidence collection

对于一个 proposed `place_order(items, supplier_id)`，收集：

```text
sku_ids = unique SKU ids in items
current_date = env.current_date
primary_start = current_date - lookback_days
fallback_start = current_date - fallback_lookback_days
```

需要的 read-only evidence：

| Evidence | Tool | Purpose |
| --- | --- | --- |
| Today's supplier quotes | `view_current_date_supplier_prices(sku_ids)` | candidate supplier set and unit price |
| Supplier-SKU returns | 对每个 candidate supplier 调用 `view_supplier_returns_avg_rate(supplier_id, start, end, sku_ids)` | primary observed quality proxy |
| Supplier-specific realized sales/profit | 对每个 candidate supplier 调用 `view_sales_profit_history(sku_ids, start, end, supplier_id=...)` | fallback quality/profit proxy |
| Inventory and pending orders | `view_inventory()` | 避免 duplicate 或 oversized ordering |
| Funds and date | `view_funds_and_date()` | affordability |
| Current SKU sale price，可选但推荐 | `view_sku_prices(sku_ids)` | margin sanity check |

因为 candidate supplier 数量通常很小，tool-call cost 是可控的。如果以后成本变高，可以按 supplier batch，并在同一天内缓存结果。

### 4.2 Candidate table schema

对每个 SKU，为每个 supplier quote 构建一行 candidate：

| Field | Source | Used for |
| --- | --- | --- |
| `sku_id` | attempted action | grouping |
| `supplier_id` | quote | candidate identity |
| `unit_price` | quote | affordability and price tie-break |
| `return_rate` | supplier returns per SKU | primary observed quality |
| `return_support` | supplier returns denominator | confidence |
| `return_rank` | computed per SKU | diagnostic and gate |
| `supplier_units_sold` | supplier-specific sales history | fallback support |
| `supplier_net_profit` | supplier-specific sales history | fallback utility |
| `supplier_profit_per_unit` | computed | fallback ranking |
| `price_rank` | computed per SKU | price-salience diagnostic |
| `policy_rank` | computed | final observed policy rank |
| `evidence_tier` | computed | `return_supported`, `profit_supported`, `price_only` |
| `evidence_confidence` | computed | `high`, `medium`, `low` |
| `selected_reason` | computed | human-readable explanation |

当 M2 block action 时，candidate table 应该返回给 LLM。同时，它也应该写入 JSONL log，方便 offline analysis。

### 4.3 Ranking policy

v1 不要使用 weighted quality formula。使用 lexicographic policy：

```text
For each SKU:
  if at least one candidate has return_support >= min_return_support:
      choose candidate with lowest return_rate
      tie-break by lower unit_price
      tie-break by higher supplier_profit_per_unit

  else if at least one candidate has supplier_units_sold >= min_profit_units:
      choose candidate with highest supplier_profit_per_unit
      tie-break by higher supplier_units_sold
      tie-break by lower unit_price

  else:
      choose lowest unit_price
      mark evidence_confidence = low
```

推荐默认值：

```json
{
  "lookback_days": 30,
  "fallback_lookback_days": 90,
  "min_return_support": 10,
  "min_profit_units": 3,
  "return_tie_epsilon": 0.02,
  "max_price_premium_when_quality_better": 0.35
}
```

具体 thresholds 应该写进 config，不要 hardcode。默认值刻意保持 conservative：

- `min_return_support=10` 避免过度相信一两个历史 returns。
- `return_tie_epsilon=0.02` 把很小的 return-rate 差异视为 tie。
- `max_price_premium_when_quality_better=0.35` 防止 quality proxy 强迫 agent 采购荒谬昂贵的 supplier。

### 4.4 Allowed deviation rules

M2 不应该盲目 block 每一个非 top supplier。当 deviation 能被 observable evidence 辩护时，它应该允许 deviation。

requested supplier 可以通过 gate，如果满足以下任一条件：

1. 它就是 recommended supplier。
2. 它的 return rate 和 recommended supplier 在 `return_tie_epsilon` 范围内接近。
3. 所有 candidates 的 return evidence 都很稀疏，并且 requested supplier 是 price rank 1。
4. recommended supplier 在当前 funds 下不可负担，而 requested supplier 能让订单可执行。
5. recommended supplier 会造成 unsafe margin，而 requested supplier 能保持非负 margin 或 configured-safe margin。

应该 block 的情况：

1. requested supplier 是 price-rank 1，但 supported return rate 明显更差。
2. requested supplier 没有 support，而另一个 supplier 有 supported lower return rate。
3. requested supplier 被用于所有 lines，只是因为 API 强制一个 supplier，但 per-SKU best suppliers 实际不同。
4. requested quantity 重复覆盖 pending inventory，或超过 demand-based coverage。
5. requested total cost 超过 funds，而且可以通过更小 quantity 或更便宜的 acceptable supplier 修正。

### 4.5 Multi-SKU orders

因为 `place_order` 接受一个 `supplier_id`，M2 必须先独立评估每个 line decision，再聚合。

Policy：

```text
For a multi-SKU order:
  allow the original order only if requested supplier is acceptable for every SKU line.
  if some SKU lines recommend different suppliers:
      block original action
      return recommended split actions grouped by supplier.
```

示例：

```json
{
  "recommended_actions": [
    {
      "name": "place_order",
      "arguments": {
        "supplier_id": "supplier_1",
        "items": [{"sku_id": "A", "quantity": 5}, {"sku_id": "B", "quantity": 3}]
      }
    },
    {
      "name": "place_order",
      "arguments": {
        "supplier_id": "supplier_4",
        "items": [{"sku_id": "C", "quantity": 2}]
      }
    }
  ]
}
```

这让 M2 在不改变环境接口的情况下兼容当前 action API。

## 5. Quantity Gate

Quantity 应该被当作 constraint 处理，而不是另一个复杂 score。

对每个 SKU：

```text
avg_daily_sales = total_units_sold_30d / observed_days
available_stock = quantity + incoming + waiting
target_qty = ceil(avg_daily_sales * target_stock_days - available_stock)
max_reasonable_qty = ceil(avg_daily_sales * max_stock_days - available_stock)
```

推荐默认值：

```json
{
  "target_stock_days": 7,
  "max_stock_days": 21,
  "absolute_min_order_qty": 1,
  "allow_zero_sales_reorder": false
}
```

Gate rules：

- 如果 requested quantity 明显大于 `max_reasonable_qty`，block 并推荐更小 quantity。
- 如果 pending/incoming 已经覆盖 target demand，block duplicate replenishment。
- 如果 sales history 为 0，根据 config 只允许小规模 exploratory order，或者默认 block。
- 如果 current funds 无法支持 recommended quantity，先 shrink quantity，再考虑切换到更差 supplier。

Quantity gate 很重要，因为仅改善 supplier-quality selection 仍然可能失败：agent 可能 overbuy、锁死 cash，最后因为 negative funds 死掉。

## 6. M2-v2 Pricing Controller

Pricing 应该是第二阶段，不是第一阶段。Supplier selection 有更强 evidence，也有更清楚的 intervention path。

对 `modify_sku_price(sku_id, new_price)`，M2 应该：

1. 收集 current price、sales/profit history、inventory cost 和 inventory age；
2. 估计一个 conservative price band；
3. block extreme prices，除非存在 observable justification。

一个简单的 v2 price band：

```text
lower_bound = max(avg_buy_cost * min_margin_multiplier, historical_good_price * 0.85)
upper_bound = historical_good_price * 1.15
```

其中 `historical_good_price` 可以沿用当前 Stage 3 analysis logic：用历史上关联更好 sales/profit outcomes 的 prices 来估计。

第一版 M2 不要把 pricing 实现成复杂 demand model。第一目标应该是减少 extreme price distance，以及明显 below-cost / overreaction errors。

## 7. Return Payload Design

当 M2 block action 时，返回：

```json
{
  "status": "e2a_action_needs_revision",
  "action_executed": false,
  "action_name": "place_order",
  "decision_id": "e2a-...",
  "attempted_action": {
    "name": "place_order",
    "arguments": {
      "supplier_id": "...",
      "items": [...]
    },
    "action_executed": false
  },
  "block_reasons": [
    "SKU 4400000004: chosen supplier supplier_3 is price-rank 1 but return-rank 5"
  ],
  "line_decisions": [...],
  "candidate_tables": {
    "4400000004": [...]
  },
  "recommended_actions": [...],
  "next_step": "Reconsider the order using the candidate table. Call place_order again only with an approved supplier/quantity."
}
```

Formatted card 应该紧凑但完整：

```text
M2 Evidence-to-Action Controller
Attempted action was not executed.

Reason:
- SKU ...: chosen supplier ... is price-rank 1 but observed-return rank 5.

Candidate table:
| SKU | Supplier | Return | Support | Units | Profit/unit | Price | Return rank | Price rank | Policy |

Recommended action:
place_order(...)
```

## 8. Logging and Analysis Artifacts

新增专门日志：

```text
e2a_decisions.jsonl
```

每一行应该包含：

```json
{
  "decision_id": "e2a-...",
  "date": "YYYY-MM-DD",
  "day": 37,
  "action_name": "place_order",
  "status": "approved|blocked",
  "attempted_action": {...},
  "block_reasons": [...],
  "line_decisions": [...],
  "recommended_actions": [...],
  "candidate_tables": {...},
  "tool_evidence_summary": [...],
  "policy_version": "observed_quality_v1",
  "config": {...}
}
```

同时保留正常的 `tool_calls.jsonl` 记录：

- `result_status = e2a_action_needs_revision`
- `action_executed = false`
- `raw.candidate_tables`
- `raw.recommended_actions`

这样后续 analysis 可以回答：

- M2 多频繁 block action？
- agent 是否 follow 了 recommended action？
- chosen supplier observed-return rank 是否改善？
- 最终 business outcomes 是改善还是变差？

## 9. Config Design

建议的 `config/e2a/retail_default.json`：

```json
{
  "e2a_mode": "gate",
  "policy_version": "observed_quality_v1",
  "actions": {
    "place_order": {
      "enabled": true,
      "lookback_days": 30,
      "fallback_lookback_days": 90,
      "min_return_support": 10,
      "min_profit_units": 3,
      "return_tie_epsilon": 0.02,
      "target_stock_days": 7,
      "max_stock_days": 21,
      "max_price_premium_when_quality_better": 0.35,
      "allow_split_recommendation": true,
      "allow_sparse_price_tiebreak": true,
      "use_current_sku_price_for_margin_gate": true,
      "min_margin_multiplier": 1.05
    },
    "modify_sku_price": {
      "enabled": false
    }
  }
}
```

CLI flags 应该和 CAEC 对齐：

```text
--e2a_mode off|gate
--e2a_config config/e2a/retail_default.json
```

Batch naming 应该包含 `e2a_{mode}`，这样 output directories 不会混淆。

## 10. Implementation Plan

### Step 1：构建 controller module

文件：

```text
module/evidence_to_action_controller.py
```

需要的方法：

```python
class EvidenceToActionController:
    VALID_MODES = {"off", "gate"}

    def __init__(self, env: Any, config: dict[str, Any]) -> None: ...

    def before_action(self, action_name: str, args: dict[str, Any]) -> dict[str, Any] | None: ...

    def _evaluate_place_order(self, args: dict[str, Any]) -> E2ADecision: ...

    def _collect_place_order_evidence(self, targets: E2ATargets) -> EvidenceBundle: ...

    def _build_supplier_candidates(self, evidence: EvidenceBundle) -> dict[str, list[SupplierCandidate]]: ...

    def _rank_candidates(self, candidates: list[SupplierCandidate]) -> list[SupplierCandidate]: ...

    def _evaluate_quantity(self, line: OrderLine, evidence: EvidenceBundle) -> QuantityDecision: ...

    def _build_recommended_actions(self, line_decisions: list[LineDecision]) -> list[dict[str, Any]]: ...

    def _format_decision_card(self, decision: E2ADecision) -> str: ...
```

尽量把文件控制在约 400 行以内。如果变得更长，把纯数据/ranking helpers 拆到：

```text
module/e2a_policy.py
```

### Step 2：接入 `RetailEnvironment`

修改：

```text
retail_environment.py
```

新增 import：

```python
from module.evidence_to_action_controller import EvidenceToActionController
```

初始化：

```python
self.e2a = EvidenceToActionController(self, config)
```

在 `place_order` 中调用：

```python
if getattr(self, "e2a", None):
    e2a_result = self.e2a.before_action(
        "place_order",
        {
            "items": normalized_items,
            "supplier_id": supplier_id,
        },
    )
    if e2a_result is not None:
        return e2a_result
```

调用位置应该在 normalized item validation 之后、确认 requested supplier 有 quote 之后，但在 mutation 之前，也在最终 funds failure 之前。

### Step 3：为 M2 添加 read-only execution helper

不要直接复用 `exec_caec_tool` 这个名字，因为 logs 应该区分 CAEC evidence 和 M2 policy evidence。

新增：

```python
def exec_e2a_tool(..., source="e2a", tag="e2a") -> Dict[str, Any]:
    ...
```

它可以和 CAEC 共用相同的 read-only dispatch。

### Step 4：泛化 runner intervention handling

修改：

```text
agents/run_react.py
agents/run_plan_and_act.py
agents/run_reflection.py
agents/run_step_reflection.py
```

重构：

```text
is_caec_evidence_status -> is_intervention_status
build_caec_tool_response_messages -> build_intervention_tool_response_messages
```

通用 helper 应该能处理两类 payload：

- CAEC evidence card
- M2 candidate-decision card

这样可以避免复制 continuation logic。

### Step 5：增加 batch launcher 支持

修改：

```text
script/run_batch_experiments.py
```

新增：

```text
--e2a_mode
--e2a_config
```

把这些参数传给 agent runners。run directory names 也要包含 mode labels。

### Step 6：添加测试

最低限度：

```text
tests/test_e2a_controller.py
```

Unit tests：

1. **Quality beats price**：supplier A 最便宜但 return 高；supplier B 有 supported lower return。M2 推荐 B。
2. **Sparse evidence falls back to price**：所有 return supports 都低于 threshold，且没有 sales/profit history。M2 允许 cheapest。
3. **Profit fallback**：return evidence 稀疏，但 supplier-specific profit/unit 更好。M2 推荐 profit/unit 更高者。
4. **Multi-SKU split**：SKU1 最佳 supplier A，SKU2 最佳 supplier B。M2 block one-supplier order，并返回两个 grouped actions。
5. **Quantity overbuy**：requested quantity 超过 demand coverage。M2 block，并推荐更小 quantity。
6. **No hidden quality**：fake quote payload 包含 `quality_score`；M2 output 不包含它，ranking 也忽略它。
7. **Action executed false**：blocked result 有 `action_executed=false` 和 `status=e2a_action_needs_revision`。

Smoke test：

```text
Run one short React episode with --e2a_mode gate and confirm:
- run does not crash;
- blocked actions appear in tool_calls.jsonl;
- blocked actions are not recorded by FCR as executed;
- e2a_decisions.jsonl is created.
```

## 11. Evaluation Protocol

尽量用相同 model/framework/config 做 baseline vs M2。

推荐 ablations：

| Variant | CAEC | M2 | Purpose |
| --- | --- | --- | --- |
| B0 | off | off | original baseline |
| B1 | gate | off | evidence completion only |
| B2 | gate | gate, supplier only | test evidence-to-supplier conversion |
| B3 | gate | gate, supplier + quantity | test procurement action quality |
| B4 | gate | gate, supplier + quantity + pricing | full M2 |

Primary metrics：

- `chosen_observed_return_rank_mean`
- `chosen_price_rank_mean`
- `observed_quality_best_hit_rate`
- `price_first_rate`
- `supplier_return_evidence_coverage`
- `post_order_return_ratio`
- `s3_action_correction`
- `survival_days`
- `final_networth`
- `total_sales`

Secondary metrics：

- M2 block rate
- revision adoption rate
- average additional tool calls per day
- token overhead
- stockout days
- duplicated pending-order rate
- quantity / 30-day average daily sales

仅作为 post-hoc diagnostic：

- raw `QualityFirst%`
- raw `QRank`

这些 post-hoc metrics 可以测试 observed proxies 是否让 decisions 更接近环境真实 quality structure，但不能把它们描述成 M2 的输入。

## 12. Expected Failure Modes

M2 可能会以几种方式失败：

1. **Cold start**：没有 supplier-specific returns 或 sales。M2 会 fallback 到 price。只要记录为 low confidence，这是可以接受的。
2. **Noisy returns**：return rate 是 delayed signal，而且 support 可能很小。需要使用 support thresholds 和 tie epsilon。
3. **Over-conservatism**：block 太多 orders 可能增加 stockouts。需要跟踪 survival、stockout days 和 total sales。
4. **One-supplier API limitation**：如果 LLM 不 follow split recommendations，multi-SKU orders 可能反复被 block。所以 runner intervention prompts 很重要。
5. **Token/tool overhead**：candidate tables 会占用 context。formatted tables 要保持紧凑，完整细节写入 JSONL。
6. **Proxy mismatch**：observed returns 不一定完美匹配 hidden quality。这没关系；论文 claim 应该落在 observable decision scaffolding，而不是 oracle access。

## 13. 我会优先实现的最小版本

如果只实现一个版本，做这些：

1. 添加 `EvidenceToActionController`。
2. 只启用 `place_order`。
3. 使用 quotes、supplier returns、supplier-specific sales/profit、inventory 和 funds 构建 candidate tables。
4. 排序时先看 observed return，再看 supplier profit/unit，最后看 price。
5. block 非 supported-quality choices，除非 deviation 有明确允许理由。
6. 当不同 SKUs 的最佳 suppliers 不一致时，推荐 split orders。
7. 记录 `e2a_decisions.jsonl`。
8. 更新 runners，让 `e2a_action_needs_revision` 像 CAEC intervention 一样处理。

除非 procurement gate 已经稳定，否则第一版 patch 不要实现 pricing controller。

## 14. Paper-Framing Claim

干净的论文 claim 是：

```text
RetailBench exposes a gap between evidence access and action formation. 
M2 addresses this gap with an observable evidence-to-action controller that 
turns scattered tool evidence into candidate comparison, constraint validation, 
and non-mutating revision feedback before business-state mutations.
```

中文意思是：

```text
RetailBench 暴露了 evidence access 和 action formation 之间的缺口。
M2 用一个可观察的 evidence-to-action controller 来弥补这个缺口：
它在业务状态被修改之前，把分散的 tool evidence 转换成 candidate comparison、
constraint validation，以及 non-mutating revision feedback。
```

最强的实验应该展示：

- CAEC 能改善 evidence availability，但不能完全修复 action quality。
- M2 能改善 observed supplier ranking，并减少 price-first procurement。
- 如果 final survival/net worth 也改善，那么结果支持 M2 是一个 practical mechanism。
- 如果 observed ranking 改善但 final outcomes 没改善，论文仍然能得到有价值的 negative result：quality-aware action conversion 必须和 quantity/cash/continuity control 结合。

## 15. 自我审视

### 这个报告是否足够有说服力？

部分足够。它把 observed Stage 3 failure 映射到了一个具体 controller design，明确规定了哪些 tools 可以用，并指出了 `place_order` 中重要的 API mismatch。

### 它是否有数据支持？

对 motivation 来说，是有支持的：已有分析显示 query depth 高但 action correction 低，同时 supplier decisions 明显偏向 price-first。报告刻意避免声称 hidden `quality_score` 是可见的。

### 它是否已经达到顶会标准的机制论证？

单靠这份设计还没有。要达到 top-conference standard，实现后还需要：

- baseline vs CAEC vs M2 ablations；
- per-action diagnostics，展示 observed-return rank improvement；
- business outcome metrics，证明 improved action conversion 不会伤害 survival 或 sales；
- qualitative examples，展示 M2 如何 block 一个 bad cheapest-supplier order，以及 revised action 为什么更好。

作为设计文档，这份报告已经可以指导实现。作为论文结果，它还需要 M2 实验支持。
