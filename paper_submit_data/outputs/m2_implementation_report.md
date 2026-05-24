# M2 Evidence-to-Action Controller Implementation Report

Date: 2026-05-23

Status: implementation design, not yet executed

## 0. Executive Summary

M2 should be implemented as an **agent-side mutation controller** between the LLM and state-changing tools. Its first version should focus on `place_order`, because Stage 3 supplier selection is the clearest failure signal: current runs often query useful evidence but still convert actions into low-price supplier choices.

The key design choice is: **do not expose or use hidden `quality_score`**. M2 should use only currently observable evidence:

- `view_current_date_supplier_prices`
- `view_supplier_returns_avg_rate`
- `view_sales_profit_history(..., supplier_id=...)`
- `view_inventory`
- `view_funds_and_date`
- optionally `view_sku_prices` for margin sanity checks

The mechanism should be **Observed-Quality-first**, not a complex weighted score. The controller builds a supplier candidate table for every ordered SKU, ranks suppliers by observable quality proxies, checks quantity and cash constraints, and either allows the order or returns a non-mutating revision card with a recommended action.

My recommendation is to implement M2 in three phases:

1. **M2-v1: Procurement Candidate Comparator + Action Gate** for `place_order`.
2. **M2-v1.1: Multi-SKU split recommendation**, because the current order API accepts one `supplier_id` for all SKUs.
3. **M2-v2: Pricing Controller** for `modify_sku_price`, after v1 proves that action conversion improves supplier decisions.

The minimal valuable version is v1. It is enough to test the core paper claim: LLM agents underperform not because evidence is unavailable, but because the agent framework lacks a structured evidence-to-action conversion layer.

## 1. What M2 Is Supposed To Fix

The current diagnosis is:

- Stage 2 evidence access is not the main bottleneck.
- Stage 3 action conversion is the main bottleneck.
- Supplier choice is systematically pulled toward the most salient local signal: unit price.
- Quality information is either not queried or queried but not converted into supplier ranking.

M2 should not try to make the LLM "smarter" in the abstract. It should convert the agent workflow from:

```text
LLM sees scattered evidence -> LLM directly calls place_order
```

to:

```text
LLM proposes place_order
-> controller builds candidate table
-> controller validates supplier, quantity, and affordability
-> action executes only if it passes
```

This is a framework intervention, not a hidden oracle.

## 2. Existing System Constraints

### 2.1 `place_order` currently has one supplier per order

Current signature:

```python
place_order(items: List[Dict[str, Any]], supplier_id: str)
```

This means one call may order multiple SKUs, but all lines must use the same supplier. M2's natural comparison is per SKU, so the controller must handle this mismatch explicitly.

If the best observed supplier differs across SKUs, M2 should not force a single supplier compromise. It should return a revision card that recommends splitting the order:

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

This detail matters. Without split recommendation, M2 may accidentally reward a single supplier that is acceptable on average but poor for individual SKUs.

### 2.2 CAEC already completes evidence, but does not rank candidates

Current CAEC checks whether required evidence is visible and, if not, returns a non-mutating evidence card. This is useful but not sufficient. CAEC answers:

```text
Has the agent seen required evidence?
```

M2 must answer:

```text
Given the evidence, is this mutation action a good action?
```

The implementation should therefore reuse CAEC patterns, but not merge M2 into CAEC conceptually. CAEC is evidence completion. M2 is policy validation.

### 2.3 Existing `place_order` internally reads hidden quality for simulation

The environment currently uses supplier quality internally when creating merchandise and order results. M2 must not use that field for decisions.

Implementation rule:

- M2 must only call public read tools.
- When parsing supplier quote payloads, M2 should whitelist only `supplier_id`, `sku_id`, and `price`.
- M2 must not call `supplier_manager.get_quality_score`.
- M2 decision logs must not contain `quality_score`.

This is important for paper fairness. `QualityFirst%` can remain a post-hoc diagnostic, but it cannot be an action-time input.

## 3. Recommended Runtime Architecture

### 3.1 New module

Add:

```text
module/evidence_to_action_controller.py
config/e2a/retail_default.json
```

Suggested class layout:

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

In `RetailEnvironment.__init__`:

```python
self.e2a = EvidenceToActionController(self, config)
```

In `place_order`, the safe order should become:

```text
1. Normalize and validate items/supplier.
2. Validate today's quote exists for requested supplier and SKU.
3. Compute requested total cost, but do not mutate state.
4. Let CAEC run first if enabled and missing visible evidence.
5. Let M2 run before mutation and before the final insufficient-funds return.
6. If M2 returns a revision card, return it with action_executed=false.
7. If M2 approves, execute existing place_order mutation.
```

Why M2 should run before final insufficient-funds return:

- If the requested supplier is unaffordable but another observable-quality acceptable supplier is cheaper, M2 can recommend a revised order.
- If the requested quantity is too large, M2 can recommend a smaller quantity.
- Returning only `"Insufficient funds"` loses the chance to convert evidence into a feasible action.

### 3.3 Runner wiring

Current runners treat CAEC statuses specially. M2 needs the same control flow.

Add a generic intervention detector:

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

Then update `run_react.py`, `run_plan_and_act.py`, `run_reflection.py`, and `run_step_reflection.py`:

- suppress the assistant message when M2 blocks an action;
- break after the blocked mutation so later tool calls in the same assistant turn do not execute;
- return the M2 card plus a reconsideration prompt to the LLM;
- keep `action_executed=false` so FCR does not record blocked actions as executed.

This is not optional. If a blocked M2 action does not stop the current tool-call batch, the agent may execute later mutation calls from the same message using stale reasoning.

## 4. M2-v1 Procurement Comparator

### 4.1 Evidence collection

For a proposed `place_order(items, supplier_id)`, collect:

```text
sku_ids = unique SKU ids in items
current_date = env.current_date
primary_start = current_date - lookback_days
fallback_start = current_date - fallback_lookback_days
```

Required read-only evidence:

| Evidence | Tool | Purpose |
| --- | --- | --- |
| Today's supplier quotes | `view_current_date_supplier_prices(sku_ids)` | candidate supplier set and unit price |
| Supplier-SKU returns | `view_supplier_returns_avg_rate(supplier_id, start, end, sku_ids)` for each candidate supplier | primary observed quality proxy |
| Supplier-specific realized sales/profit | `view_sales_profit_history(sku_ids, start, end, supplier_id=...)` for each candidate supplier | fallback quality/profit proxy |
| Inventory and pending orders | `view_inventory()` | avoid duplicate or oversized ordering |
| Funds and date | `view_funds_and_date()` | affordability |
| Current SKU sale price, optional but recommended | `view_sku_prices(sku_ids)` | margin sanity check |

Tool-call cost is manageable because the candidate supplier count is small. If it becomes expensive, batch by supplier and cache within the same day.

### 4.2 Candidate table schema

For each SKU, build one candidate row per supplier quote:

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

The candidate table should be returned to the LLM whenever M2 blocks an action. It should also be written to a JSONL log for offline analysis.

### 4.3 Ranking policy

Do not use a weighted quality formula in v1. Use a lexicographic policy:

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

Recommended defaults:

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

The exact thresholds should be config values, not hardcoded. The defaults are intentionally conservative:

- `min_return_support=10` avoids over-trusting one or two historical returns.
- `return_tie_epsilon=0.02` treats small return-rate differences as ties.
- `max_price_premium_when_quality_better=0.35` prevents a quality proxy from forcing absurdly expensive procurement.

### 4.4 Allowed deviation rules

M2 should not blindly block every non-top supplier. It should allow deviations when they are defensible from observable evidence.

A requested supplier can pass if any of these holds:

1. It is the recommended supplier.
2. Its return rate is tied with the recommended supplier within `return_tie_epsilon`.
3. Return evidence is sparse for all candidates and the requested supplier is price rank 1.
4. The recommended supplier is unaffordable under current funds, while the requested supplier keeps the order feasible.
5. The recommended supplier would create unsafe margin, while the requested supplier keeps non-negative or configured-safe margin.

It should block when:

1. Requested supplier is price-rank 1 but has clearly worse supported return rate.
2. Requested supplier has no support while another supplier has supported lower return rate.
3. Requested supplier is selected for all lines only because the API forces one supplier, but per-SKU best suppliers differ.
4. Requested quantity duplicates pending inventory or exceeds demand-based coverage.
5. Requested total cost exceeds funds and a smaller quantity or cheaper acceptable supplier is available.

### 4.5 Multi-SKU orders

Because `place_order` takes one `supplier_id`, M2 must evaluate line decisions independently and then aggregate.

Policy:

```text
For a multi-SKU order:
  allow the original order only if requested supplier is acceptable for every SKU line.
  if some SKU lines recommend different suppliers:
      block original action
      return recommended split actions grouped by supplier.
```

Example:

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

This makes M2 compatible with the current action API without changing the environment interface.

## 5. Quantity Gate

Quantity should be handled as a constraint, not as another complex score.

For each SKU:

```text
avg_daily_sales = total_units_sold_30d / observed_days
available_stock = quantity + incoming + waiting
target_qty = ceil(avg_daily_sales * target_stock_days - available_stock)
max_reasonable_qty = ceil(avg_daily_sales * max_stock_days - available_stock)
```

Recommended defaults:

```json
{
  "target_stock_days": 7,
  "max_stock_days": 21,
  "absolute_min_order_qty": 1,
  "allow_zero_sales_reorder": false
}
```

Gate rules:

- If requested quantity is much larger than `max_reasonable_qty`, block and recommend a smaller quantity.
- If pending/incoming already covers target demand, block duplicate replenishment.
- If sales history is zero, allow only small exploratory order or block by default, depending on config.
- If current funds cannot support the recommended quantity, shrink quantity before switching to a worse supplier.

The quantity gate is important because supplier-quality selection alone can still fail if the agent overbuys, locks cash, and dies from negative funds.

## 6. M2-v2 Pricing Controller

Pricing should be the second phase, not the first. Supplier selection has stronger evidence and a clearer intervention path.

For `modify_sku_price(sku_id, new_price)`, M2 should:

1. collect current price, sales/profit history, inventory cost, and inventory age;
2. estimate a conservative price band;
3. block extreme prices unless there is observable justification.

A simple v2 price band:

```text
lower_bound = max(avg_buy_cost * min_margin_multiplier, historical_good_price * 0.85)
upper_bound = historical_good_price * 1.15
```

Where `historical_good_price` can be estimated from the existing Stage 3 analysis logic: historical prices associated with better sales/profit outcomes.

Do not implement pricing as a complex demand model in the first M2 version. The first target should be to reduce extreme price distance and obvious below-cost / overreaction errors.

## 7. Return Payload Design

When M2 blocks an action, return:

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

The formatted card should be compact but complete:

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

Add a dedicated log:

```text
e2a_decisions.jsonl
```

Each line should contain:

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

Also keep the normal `tool_calls.jsonl` record with:

- `result_status = e2a_action_needs_revision`
- `action_executed = false`
- `raw.candidate_tables`
- `raw.recommended_actions`

This allows later analysis to answer:

- How often did M2 block an action?
- Did the agent follow the recommended action?
- Did chosen supplier observed-return rank improve?
- Did final business outcomes improve or degrade?

## 9. Config Design

Proposed `config/e2a/retail_default.json`:

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

CLI flags should mirror CAEC:

```text
--e2a_mode off|gate
--e2a_config config/e2a/retail_default.json
```

Batch naming should include `e2a_{mode}` so output directories remain distinguishable.

## 10. Implementation Plan

### Step 1: Build the controller module

File:

```text
module/evidence_to_action_controller.py
```

Required methods:

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

Keep the file below roughly 400 lines. If it grows, split pure data/ranking helpers into:

```text
module/e2a_policy.py
```

### Step 2: Wire into `RetailEnvironment`

Modify:

```text
retail_environment.py
```

Add import:

```python
from module.evidence_to_action_controller import EvidenceToActionController
```

Initialize:

```python
self.e2a = EvidenceToActionController(self, config)
```

In `place_order`, call:

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

Placement should be after normalized item validation and after confirming the requested supplier has quotes, but before mutation and before final funds failure.

### Step 3: Add read-only execution helper for M2

Do not reuse `exec_caec_tool` name directly, because logs should distinguish CAEC evidence from M2 policy evidence.

Add:

```python
def exec_e2a_tool(..., source="e2a", tag="e2a") -> Dict[str, Any]:
    ...
```

It can share the same read-only dispatch as CAEC.

### Step 4: Generalize runner intervention handling

Modify:

```text
agents/run_react.py
agents/run_plan_and_act.py
agents/run_reflection.py
agents/run_step_reflection.py
```

Refactor:

```text
is_caec_evidence_status -> is_intervention_status
build_caec_tool_response_messages -> build_intervention_tool_response_messages
```

The generic helper should handle both payloads:

- CAEC evidence card
- M2 candidate-decision card

This avoids duplicating continuation logic.

### Step 5: Add batch launcher support

Modify:

```text
script/run_batch_experiments.py
```

Add:

```text
--e2a_mode
--e2a_config
```

Pass these to agent runners. Include mode labels in run directory names.

### Step 6: Add tests

At minimum:

```text
tests/test_e2a_controller.py
```

Unit tests:

1. **Quality beats price**: supplier A is cheapest but high return; supplier B has supported lower return. M2 recommends B.
2. **Sparse evidence falls back to price**: all return supports below threshold and no sales/profit history. M2 allows cheapest.
3. **Profit fallback**: return evidence sparse, but supplier-specific profit/unit is better. M2 recommends higher profit/unit.
4. **Multi-SKU split**: SKU1 best supplier A, SKU2 best supplier B. M2 blocks one-supplier order and returns two grouped actions.
5. **Quantity overbuy**: requested quantity exceeds demand coverage. M2 blocks and recommends smaller quantity.
6. **No hidden quality**: fake quote payload contains `quality_score`; M2 output does not contain it and ranking ignores it.
7. **Action executed false**: blocked result has `action_executed=false` and `status=e2a_action_needs_revision`.

Smoke test:

```text
Run one short React episode with --e2a_mode gate and confirm:
- run does not crash;
- blocked actions appear in tool_calls.jsonl;
- blocked actions are not recorded by FCR as executed;
- e2a_decisions.jsonl is created.
```

## 11. Evaluation Protocol

Use baseline vs M2 with the same model/framework/config where possible.

Recommended ablations:

| Variant | CAEC | M2 | Purpose |
| --- | --- | --- | --- |
| B0 | off | off | original baseline |
| B1 | gate | off | evidence completion only |
| B2 | gate | gate, supplier only | test evidence-to-supplier conversion |
| B3 | gate | gate, supplier + quantity | test procurement action quality |
| B4 | gate | gate, supplier + quantity + pricing | full M2 |

Primary metrics:

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

Secondary metrics:

- M2 block rate
- revision adoption rate
- average additional tool calls per day
- token overhead
- stockout days
- duplicated pending-order rate
- quantity / 30-day average daily sales

Post-hoc diagnostic only:

- raw `QualityFirst%`
- raw `QRank`

Those post-hoc metrics can test whether observed proxies move decisions closer to the environment's true quality structure, but they must not be described as inputs to M2.

## 12. Expected Failure Modes

M2 can fail in several ways:

1. **Cold start**: no supplier-specific returns or sales exist. M2 will fall back to price. This is acceptable if logged as low confidence.
2. **Noisy returns**: return rate is delayed and support may be small. Use support thresholds and tie epsilon.
3. **Over-conservatism**: blocking too many orders may increase stockouts. Track survival, stockout days, and total sales.
4. **One-supplier API limitation**: multi-SKU orders may be repeatedly blocked if the LLM does not follow split recommendations. This is why runner intervention prompts matter.
5. **Token/tool overhead**: candidate tables cost context. Keep formatted tables compact and write full detail to JSONL.
6. **Proxy mismatch**: observed returns may not perfectly match hidden quality. That is okay; the paper claim should be about observable decision scaffolding, not oracle access.

## 13. Minimal Version I Would Implement First

If only one version is implemented, do this:

1. Add `EvidenceToActionController`.
2. Enable only `place_order`.
3. Build candidate tables using quotes, supplier returns, supplier-specific sales/profit, inventory, and funds.
4. Rank by observed return first, then supplier profit/unit, then price.
5. Block non-supported-quality choices unless deviation is explicitly allowed.
6. Recommend split orders when best suppliers differ across SKUs.
7. Log `e2a_decisions.jsonl`.
8. Update runners so `e2a_action_needs_revision` behaves like CAEC intervention.

Do not implement the pricing controller in the first patch unless the procurement gate is already stable.

## 14. Paper-Framing Claim

The clean paper claim is:

```text
RetailBench exposes a gap between evidence access and action formation. 
M2 addresses this gap with an observable evidence-to-action controller that 
turns scattered tool evidence into candidate comparison, constraint validation, 
and non-mutating revision feedback before business-state mutations.
```

The strongest experiment would show:

- CAEC improves evidence availability but does not fully fix action quality.
- M2 improves observed supplier ranking and reduces price-first procurement.
- If final survival/net worth also improves, the result supports M2 as a practical mechanism.
- If observed ranking improves but final outcomes do not, the paper still gets a useful negative result: quality-aware action conversion must be coupled with quantity/cash/continuity control.

## 15. Self-Review

### Is this report convincing enough?

Partly yes. It maps the observed Stage 3 failure to a concrete controller design, specifies exactly which tools are allowed, and identifies the important API mismatch in `place_order`.

### Does it have data support?

Yes for the motivation: existing analysis shows high query depth but low action correction, and supplier decisions skew toward price-first. The report deliberately avoids claiming that hidden `quality_score` is visible.

### Is it top-conference-ready as a mechanism?

Not yet by itself. To reach a top-conference standard, the implementation needs:

- baseline vs CAEC vs M2 ablations;
- per-action diagnostics showing observed-return rank improvement;
- business outcome metrics showing that improved action conversion does not harm survival or sales;
- qualitative examples where M2 blocks a bad cheapest-supplier order and the revised action is better.

As a design document, this is ready to guide implementation. As a paper result, it still needs the M2 experiment.
