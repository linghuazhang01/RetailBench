# Four-Stage Measurement Framework for RetailBench Agent Behavior

## Goal

This note turns the four-stage operational difficulty of RetailBench into measurable analysis targets. The purpose is to explain why LLM agents underperform, quantify where the failures happen, and connect each failure mode to possible improvements.

The four stages are:

1. SKU candidate selection: decide which SKUs deserve attention today.
2. Evidence acquisition: collect inventory, sales, supplier, price, reviews, returns, and news evidence for those SKUs.
3. Action conversion: transform evidence into valid and useful orders or price changes.
4. Temporal follow-up: revisit earlier decisions and adapt based on delayed feedback.

## Stage 1: SKU Candidate Selection

### What We Want to Measure

The agent should identify the SKUs that matter today. Relevant SKUs may have high demand, low inventory, high margin, abnormal returns, review drift, supplier opportunity, news exposure, or recent stockout.

### Quantitative Metrics

| Metric | Definition | Data Source | Interpretation |
| --- | --- | --- | --- |
| Daily acted SKU count | Number of distinct SKUs that receive `place_order` or `modify_sku_price` each day | `tool_calls.jsonl` | Higher means broader operational coverage |
| Daily sold SKU count | Number of distinct SKUs with positive sales each day | environment daily logs / parsed metrics | Measures realized product-space coverage |
| Action-to-sales SKU overlap | Fraction of acted SKUs that later sell within the next K days | action logs + sales records | Measures whether selected SKUs were operationally relevant |
| Missed high-demand SKU rate | Fraction of high-sales or stockout SKUs that receive no attention in a window | sales records + action logs | Detects under-coverage of important products |
| Repeated narrow-focus index | Concentration of actions over SKUs, e.g. Herfindahl index or top-10 SKU action share | action logs | High value means the agent over-focuses on a small SKU subset |
| Category coverage | Number of product categories touched per day/window | SKU metadata + action logs | Measures whether the agent scales beyond a few categories |

### Qualitative Diagnostics

- Inspect whether the agent explains why it picked those SKUs.
- Check whether candidate SKUs reflect real inventory pressure, demand, returns, reviews, or news.
- Compare a strong run and weak run: does the weak run repeatedly revisit a tiny SKU set?

### Current Evidence

`analyze_four_stage_metrics.py` now computes direct acted-SKU coverage for all 20 packaged runs. In the latest report, survival-first selected LLM runs span 0.95 to 7.89 acted SKUs/day, while the Non-LLM Heuristic reaches 16.74 acted SKUs/day. Missed high-demand rate is also much lower for the heuristic (0.205) than for most LLM runs, supporting the claim that weak LLM agents often fail before evidence acquisition because important SKUs never enter the action set.

### Possible Solutions

- Add an explicit SKU candidate generator before each daily decision.
- Require the agent to maintain a daily watchlist with reasons and expected follow-up.
- Use retrieval over prior SKU outcomes to avoid repeatedly forgetting important SKUs.

## Stage 2: Evidence Acquisition

### What We Want to Measure

The agent should collect the evidence needed before mutating the environment. For orders, it should know inventory, recent sales, supplier quotes, supplier quality or return proxy, cash, and delivery timing. For pricing, it should know current price, cost, demand, inventory pressure, and recent profit.

### Quantitative Metrics

| Metric | Definition | Data Source | Interpretation |
| --- | --- | --- | --- |
| Query depth | Fraction of required evidence categories queried before an action | `evaluate_final_metrics.py` | Higher means better evidence coverage |
| Evidence completeness by action type | Query depth split by `place_order` and `modify_sku_price` | tool logs | Identifies whether procurement or pricing is weaker |
| Evidence freshness | Time gap between evidence query and action | tool logs | Old evidence is less reliable in dynamic environments |
| Evidence-action SKU match | Whether queried evidence concerns the same SKU that receives action | tool logs | Detects irrelevant or unfocused querying |
| Missing critical evidence rate | Fraction of actions missing one or more required evidence categories | tool logs | Direct measure of unsafe action preparation |

### Qualitative Diagnostics

- Read action traces where query depth is high but action correction is low.
- Check whether the model misinterprets evidence or simply ignores it.
- Identify whether the agent uses reviews/news as decision inputs or as decorative context.

### Current Evidence

Selected LLM runs can have high query depth but still low action correction. GPT-5.5 has query depth 0.992 and action correction 0.444; DeepSeek-V4-Pro has query depth 0.928 but action correction 0.271. This means evidence acquisition alone does not solve the task.

### Possible Solutions

- Add evidence-gated action templates.
- Make each mutating action cite the exact evidence it depends on.
- Penalize irrelevant evidence queries that do not match the acted SKU.

## Stage 3: Action Conversion

### What We Want to Measure

The agent should convert evidence into correct actions: choose a good supplier, order a reasonable quantity, and set a reasonable price.

### Quantitative Metrics

| Metric | Definition | Data Source | Interpretation |
| --- | --- | --- | --- |
| Action correction | Fraction of evaluable actions matching the visible-information policy | `evaluate_final_metrics.py` | Measures whether evidence becomes correct action |
| Raw quality top-1 hit | Whether selected supplier has the highest hidden raw quality | supplier diagnostics | Measures supplier-quality alignment |
| Quality regret | Gap between best supplier quality and selected supplier quality | supplier diagnostics | Lower is better |
| Quality ratio | Selected supplier quality divided by best supplier quality | supplier diagnostics | Higher is better |
| Price distance | Percent distance between selected price and estimated optimal price | pricing diagnostics | Lower is better |
| Order quantity / avg daily sales | Order size relative to recent demand | order diagnostics | Too high risks expiration; too low risks stockout |
| Invalid or blocked action rate | Fraction of rejected or semantically invalid actions | tool logs | Measures schema/control reliability |

### Qualitative Diagnostics

- Inspect top failure cases for supplier choice: did the agent prefer low price over quality, ignore returns, or miss delivery delay?
- Inspect price failures: did the agent change prices without margin/demand evidence?
- Inspect quantity failures: did the agent over-order due to stockout panic or under-order due to cash fear?

### Current Evidence

Selected LLM action correction ranges from about 0.16 to 0.44. Raw quality top-1 hit ranges from about 0.125 to 0.346. Price distance can be very large, especially for Grok-4.3. These diagnostics strongly suggest that the primary bottleneck is not tool access but evidence-to-action conversion.

### Possible Solutions

- Add a deterministic action validator.
- Add a supplier-quality memory or learned supplier scorer.
- Use constrained action decoding for price and quantity ranges.
- Add policy distillation from non-LLM heuristic trajectories without exposing hidden state at inference time.

## Stage 4: Temporal Follow-Up

### What We Want to Measure

The agent should revisit earlier decisions and adapt as delayed effects arrive. Orders arrive later, reviews and returns appear after sales, news effects persist, and inventory ages over time.

### Quantitative Metrics

| Metric | Definition | Data Source | Interpretation |
| --- | --- | --- | --- |
| Follow-up rate | Fraction of acted SKUs revisited within K days | action logs | Measures whether decisions are monitored |
| Unresolved SKU rate | SKUs with stockout/returns/expiration but no later follow-up | sales + returns + action logs | Detects forgotten problems |
| Focus continuity | Similarity between daily target SKU sets over a rolling window | action logs | Measures whether the agent maintains stable focus |
| Response latency | Days between signal event and corrective action | event logs + action logs | Lower means faster adaptation |
| Delayed feedback assimilation | Whether returns/reviews/news later affect supplier or pricing decisions | logs + text traces | Measures temporal credit assignment |
| Repeated error rate | Same SKU suffers repeated stockout, return, or expiration without policy change | environment logs | Detects failure to learn from feedback |

### Qualitative Diagnostics

- Pick a SKU with repeated return or stockout and trace whether the agent recognizes the pattern.
- Compare strong and weak runs on whether they revisit prior decisions.
- Inspect whether daily notes/memory preserve concrete SKU-level commitments.

### Current Evidence

`analyze_four_stage_metrics.py` now computes follow-up rate, response latency, focus continuity, unresolved event rate, and repeated-error-without-intervention rate. The latest report shows that strong survival runs often revisit acted SKUs through query/action traces, but their follow-up action rate is still much lower than the heuristic's direct action follow-up. Weak runs such as GLM-5.1 plan-and-act and Grok-4.3 plan-and-act show high unresolved event rates, indicating that delayed stockout, return, or expiration signals can remain open for multiple days.

### Possible Solutions

- Add a persistent SKU watchlist with status fields: `open`, `monitor`, `resolved`, `escalate`.
- Require next-day follow-up for any SKU that was ordered, repriced, stocked out, returned, or affected by news.
- Add a temporal controller that schedules mandatory revisit checks.

## Cross-Stage Interpretation

The four stages are not independent. A failure in an earlier stage causes downstream failures:

1. Poor SKU selection means the agent never queries evidence for important SKUs.
2. Poor evidence acquisition means actions are based on partial information.
3. Poor action conversion turns good evidence into bad orders or prices.
4. Poor follow-up prevents the agent from correcting delayed consequences.

This is why RetailBench produces large model differences. The benchmark amplifies small daily differences into large survival, sales, and net-worth gaps.

## Proposed Analysis Plan

### Step 1: Add Metrics

Add metric extractors for:

- acted SKU count,
- action-to-sales overlap,
- missed high-demand SKU rate,
- repeated focus concentration,
- evidence freshness,
- evidence-action SKU match,
- follow-up rate,
- unresolved SKU rate,
- response latency,
- repeated error rate.

### Step 2: Build Per-Stage Tables

Create one table per stage:

- Stage 1 table: candidate selection and coverage.
- Stage 2 table: evidence completeness.
- Stage 3 table: action correctness.
- Stage 4 table: follow-up and temporal stability.

### Step 3: Compare Strong and Weak Runs

Use survival-first selected runs:

- Strong examples: GPT-5.5 / react, DeepSeek-V4-Pro / plan_and_act.
- Weak examples: Grok-4.3 / react, GLM-5.1 / react.
- Mid examples: Kimi-K2.6 / react, MiniMax-M2.5 / plan_and_act, Qwen3.5-397B-A17B / reflection.

### Step 4: Write Failure Mechanisms

For each model, describe where it breaks:

- Does it fail to select enough SKUs?
- Does it query evidence but fail to use it?
- Does it choose poor suppliers?
- Does it over/under-order?
- Does it fail to follow up?

### Step 5: Connect to Interventions

Each proposed solution should target one stage and predict metric movement:

| Intervention | Target Stage | Expected Metric Movement |
| --- | --- | --- |
| SKU candidate generator | Stage 1 | Higher acted SKU count, higher sold SKU coverage, fewer missed high-demand SKUs |
| Evidence-gated action protocol | Stage 2 | Higher query depth, lower missing critical evidence rate |
| Supplier-quality memory | Stage 3 | Higher raw quality top-1 hit, lower return ratio |
| Action validator | Stage 3 | Higher action correction, lower price distance, fewer invalid actions |
| SKU watchlist and scheduler | Stage 4 | Higher follow-up rate, lower unresolved SKU rate |
| Hierarchical policy | All stages | Better strategy consistency, survival, and lower variance |

## Paper Claim Template

A conservative paper-ready claim could be:

RetailBench reveals that tool-enabled LLM agents fail through a multi-stage operational pipeline. Agents must choose relevant products, collect appropriate evidence, convert evidence into constrained retail actions, and revisit delayed outcomes. Current agents show measurable weaknesses across these stages: broad variation in SKU coverage and survival, high query depth but low action correction, weak supplier-quality selection, and qualitative focus drift. This suggests that future progress requires explicit candidate generation, evidence-gated actions, supplier-quality memory, constraint-aware controllers, and temporal follow-up mechanisms rather than larger prompts alone.

## Current Implementation Status

The four-stage metric extractor is implemented in `paper_submit_data/analyze_four_stage_metrics.py`, with all-run action-conversion support factored into `paper_submit_data/four_stage_action_metrics.py`.

Generated artifacts:

- `outputs/four_stage_metrics.csv`: one row per run with stage-level numeric metrics.
- `outputs/four_stage_metrics.json`: machine-readable version with selected best runs.
- `outputs/four_stage_analysis_report.md`: human-readable report with one table and one problem summary per stage.

Current remaining limits are about claim strength, not missing basic extraction. The diagnostics are descriptive and mechanism-oriented; they are not causal proof. The correlation table uses only seven survival-first selected LLM runs, so it should be framed as evidence for mechanism hypotheses rather than statistical significance. Non-LLM action conversion remains N/A because it has no `tool_calls.jsonl`; it is still comparable on outcome, SKU coverage, order follow-up, sales, returns, and expiration behavior.

Before promoting the analysis directly into the paper, the next useful additions are:

- Add confidence intervals or bootstrap sensitivity for model-level comparisons where repeated runs exist.
- Add a few trace-level case studies for representative Stage 2-to-Stage 3 failures.
- Add an explicit causal caveat for retrospective metrics such as missed high-demand SKU rate and action-to-sales overlap.
