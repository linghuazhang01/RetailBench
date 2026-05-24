# Why LLM Agents Underperform in RetailBench

## Version 1: Initial Evidence-First Report

### Scope

This report analyzes why current LLM agents perform poorly on RetailBench and why their behavior remains far below a non-LLM quality-based heuristic reference. The evidence comes from the survival-first selected runs in `paper_submit_data`, including seven selected LLM runs and one non-LLM heuristic run.

This is a descriptive analysis, not a statistical significance claim. Each model contributes one selected run, chosen by maximum survival days with final net worth and total sales as tie-breakers.

### Core Finding

LLM agents underperform because RetailBench requires stable long-horizon retail operation rather than isolated tool-use success. The main failure is not that agents cannot call tools; it is that they do not consistently transform partial observations into a durable operating policy over SKUs, suppliers, prices, inventory, reviews, and cash constraints.

The selected LLM runs vary widely:

| Model | Selected Framework | Survival Days | Final Net Worth | Total Sales | Daily Sold SKUs |
| --- | --- | --- | --- | --- | --- |
| DeepSeek-V4-Pro | plan_and_act | 180 | 10,120.90 | 164,417 | 32.57 |
| GPT-5.5 | react | 180 | 24,350.98 | 136,405 | 32.27 |
| Kimi-K2.6 | react | 130 | 792.19 | 86,214 | 25.73 |
| MiniMax-M2.5 | plan_and_act | 73 | -397.95 | 23,521 | 12.08 |
| Qwen3.5-397B-A17B | reflection | 71 | 1,183.17 | 35,622 | 26.17 |
| GLM-5.1 | react | 60 | -2,134.36 | 7,016 | 3.97 |
| Grok-4.3 | react | 58 | 828.40 | 11,305 | 6.74 |
| Non-LLM Heuristic | quality_based | 180 | 131,510.42 | 267,998 | 34.48 |

Survival days range from 58 to 180 for selected LLM runs. Total sales range from 7,016 to 164,417. Daily sold SKU coverage ranges from 3.97 to 32.57. This spread is too large to explain by final reward alone; it reflects differences in operational stability.

### Why Model Differences Are Large

First, long-horizon compounding magnifies small daily errors. In RetailBench, stock decisions affect future availability, supplier choices affect returns and reviews, prices affect demand, and cash constraints limit later actions. A model that under-covers SKUs for several days can lose sales, miss demand signals, weaken future evidence, and then make poorer procurement decisions.

Second, SKU coverage is a major separator. The strong selected LLM runs sell roughly 32 distinct SKUs per day, while weak runs sell fewer than 7. Low SKU coverage means the agent is not operating the full 96-SKU, 20-category store. This matches the observed failure mode where agents repeatedly focus on a narrow subset of SKUs.

Third, evidence gathering is not enough. Action diagnostics show high query depth can coexist with weak action correction:

| Model | Query Depth | Action Correction | Raw Quality Top-1 Hit | Quality Ratio | Price Distance (%) |
| --- | --- | --- | --- | --- | --- |
| GPT-5.5 | 0.992 | 0.444 | 0.346 | 0.777 | 17.92 |
| DeepSeek-V4-Pro | 0.928 | 0.271 | 0.233 | 0.660 | 25.46 |
| Kimi-K2.6 | 0.906 | 0.245 | 0.125 | 0.543 | 22.07 |
| MiniMax-M2.5 | 0.785 | 0.176 | 0.140 | 0.643 | 26.99 |
| Qwen3.5-397B-A17B | 0.755 | 0.162 | 0.169 | 0.614 | 26.45 |
| GLM-5.1 | 0.833 | 0.165 | 0.319 | 0.798 | 36.49 |
| Grok-4.3 | 0.566 | 0.167 | 0.141 | 0.633 | 110.79 |

The best action correction among selected LLM runs is only 0.444. Raw quality top-1 hit is only 0.125 to 0.346. Thus, even when agents query relevant information, they often choose suboptimal suppliers or prices.

### Why the Oracle-Style Heuristic Is Much Better

The non-LLM heuristic reaches 180 days, 131,510.42 final net worth, and 267,998 total sales. It is not a fair LLM baseline because it is a hand-coded quality-based operating policy. It encodes stable assumptions about supplier quality, replenishment rhythm, shelf assortment, and bulk ordering.

The gap remains large even for LLM runs that survive 180 days:

| Model | Survival Gap | Net Worth Gap | Sales Gap | Return Ratio Over Heuristic | Expired Ratio Over Heuristic |
| --- | --- | --- | --- | --- | --- |
| DeepSeek-V4-Pro | 0 | 121,389.51 | 103,581 | 0.0657 | 0.0086 |
| GPT-5.5 | 0 | 107,159.43 | 131,593 | 0.0444 | 0.0055 |

This means survival alone does not solve the benchmark. LLM agents still lose value through weaker supplier selection, less efficient pricing, higher returns, and poorer inventory turnover.

### Initial Improvement Strategies

1. Add SKU candidate generation before action selection. The agent should first decide which SKUs deserve attention using sales velocity, stockout risk, margin, inventory age, supplier availability, review drift, and news exposure.

2. Add an evidence checklist for each mutating action. A `place_order` action should require inventory, recent demand, supplier quote, supplier quality or return proxy, and cash status. A `modify_sku_price` action should require current price, cost, sales history, inventory pressure, and profit signal.

3. Add a supplier-quality memory. The agent should maintain a persistent supplier-SKU quality estimate, updated from returns and reviews, rather than rediscovering supplier reliability every day.

4. Add an action validator. Before executing an order or price update, a controller should reject actions with extreme quantities, missing evidence, poor supplier quality, or price changes far from historical optima.

5. Add long-horizon learning or policy distillation. The heuristic can be used to generate trajectories for imitation learning, reward modeling, or retrieval-guided policy improvement.

## Self-Review After Version 1

### Is Version 1 Persuasive?

Partially. It identifies plausible failure modes and supports them with survival, sales, SKU coverage, action correction, and supplier-quality diagnostics. It also correctly separates the heuristic as an oracle-style reference rather than a fair baseline.

### Is Version 1 Sufficiently Data-Supported?

Moderately. It uses the strongest available tables, but the causal chain is still under-developed. It says long-horizon compounding matters, but it does not clearly connect each metric to the benchmark mechanism. It also does not distinguish stable conclusions from hypotheses that require ablations.

### Does Version 1 Match Top-Conference Standards?

Not yet. A top-conference report needs a sharper thesis, clearer threat model, stronger mechanism decomposition, and more testable improvement proposals. Version 1 is closer to an internal experiment note than a paper-ready analysis section. It also risks sounding like a list of observations rather than an argument.

### What Must Be Improved?

1. Reframe the analysis around a central thesis: RetailBench exposes the gap between information access and operational policy formation.
2. Make the failure taxonomy more mechanistic: coverage failure, evidence-to-action failure, supplier-quality failure, temporal credit assignment failure, and constraint/control failure.
3. Separate supported findings, plausible mechanisms, and proposed interventions.
4. Turn improvement strategies into testable research hypotheses with expected metric movement.
5. Avoid overclaiming from one selected run per model.

## Version 2: Revised Paper-Oriented Report

### Thesis

RetailBench shows that current LLM agents struggle not because they lack access to tools, but because tool access does not automatically become a stable operating policy. The benchmark requires agents to repeatedly select which products matter, gather the right evidence, translate evidence into supplier, quantity, and price actions, and preserve consistency as delayed consequences arrive through sales, returns, reviews, inventory aging, and cash flow. Current LLM agents fail at different points in this pipeline, which explains both the large variation across models and the persistent gap to the oracle-style heuristic.

### Evidence Base

The analysis uses survival-first selected runs for seven LLMs and one quality-based non-LLM heuristic. The environment has 96 SKUs, 20 categories, five suppliers per SKU, review feedback, daily news, dynamic supplier price-quality profiles, a 30,000 initial budget, 600 daily rent, 15,000 inventory capacity, and 40-SKU shelf capacity.

The selected LLM runs are not repeated seeds, so the analysis should be read as descriptive evidence. The stable conclusion is that current agents show large operational weaknesses. More specific causal claims should be validated by ablations.

### Finding 1: The Main Gap Is Operational Policy Formation, Not Tool Availability

The selected LLM runs often inspect evidence, but action-level correctness remains low. GPT-5.5 has the highest query depth at 0.992 and the highest action correction at 0.444; DeepSeek-V4-Pro has query depth 0.928 but action correction only 0.271. Other selected runs have action correction near 0.16-0.25. This pattern indicates that agents can access observations but struggle to convert them into robust business actions.

This distinction matters because RetailBench actions are coupled. `place_order` requires balancing demand, current inventory, supplier price, supplier quality, delivery delay, return risk, and cash. `modify_sku_price` requires balancing price, cost, demand, inventory pressure, and profit. A shallow interpretation of tool-use success would miss this gap.

### Finding 2: Product-Space Coverage Controls the Effective Operating Horizon

RetailBench is a product-space scaling problem. Strong selected LLM runs sell around 32 distinct SKUs per day, while weak selected runs sell 3.97 to 12.08. Total sales track this coverage difference: DeepSeek-V4-Pro reaches 164,417 total sales and GPT-5.5 reaches 136,405, while GLM-5.1 and Grok-4.3 remain at 7,016 and 11,305.

This suggests that agents fail when they cannot maintain a daily candidate set that covers enough high-value SKUs. The problem is not just choosing the right action for a given SKU; it is deciding which SKUs deserve attention under limited context, time, and budget.

Supported claim: SKU coverage is strongly associated with survival and sales in the selected runs.

Hypothesis requiring ablation: adding an explicit SKU candidate generator should improve daily sold SKU coverage and total sales, especially for agents with low baseline coverage.

### Finding 3: Supplier Quality Is a Hidden Long-Horizon Bottleneck

The raw supplier-quality diagnostics show a large gap between observed decisions and the quality-aware heuristic. Selected LLM raw quality top-1 hit ranges from 0.125 to 0.346, and quality ratio ranges from 0.543 to 0.798. This means agents frequently select suppliers that are far from the hidden raw quality optimum.

This error compounds. Low-quality suppliers increase returns, hurt reviews, reduce future demand, and waste cash. The selected LLM return ratios range from 0.0646 to 0.1347, while the heuristic return ratio is 0.0201. Even the best 180-day LLM runs have higher return pressure than the heuristic.

Supported claim: selected LLMs choose quality-optimal suppliers much less often than a quality-aware policy would prefer.

Hypothesis requiring ablation: a supplier-quality memory or learned supplier scorer should lower return ratio and improve final net worth.

### Finding 4: Long-Horizon Compounding Explains Large Model Differences

Small daily errors in RetailBench become large trajectory differences. The selected LLM survival range is 58-180 days. Survival correlates strongly with total sales in the selected runs, and action correction is strongly correlated with final net worth. This is consistent with a compounding process: better action conversion improves replenishment and pricing, which improves sales and cash, which allows further inventory investment and broader SKU coverage.

The reverse also occurs. Poor SKU coverage leads to low sales and weak feedback. Poor supplier choice leads to returns and review degradation. Poor price changes distort demand. Expiration and stockouts consume operational slack. Once cash becomes constrained, the agent has fewer recovery options.

Supported claim: weak runs fail through interacting operational channels rather than one isolated error type.

Hypothesis requiring ablation: decomposing policies into stable modules should reduce variance across models and scaffolds.

### Finding 5: The Oracle-Style Heuristic Is Better Because It Encodes a Policy, Not Because It Reasons Better

The heuristic reaches 131,510.42 final net worth and 267,998 sales. This should not be framed as a fair baseline. It is engineered with domain knowledge and stable operating rules. Its advantage is structural: it does not need to infer a long-horizon policy from text observations, and it does not suffer from prompt drift, context compression, schema errors, or inconsistent SKU focus.

The gap remains even when LLMs survive all 180 days. DeepSeek-V4-Pro and GPT-5.5 both survive 180 days, but their final net worth remains lower than the heuristic by 121,389.51 and 107,159.43. This shows that survival is necessary but not sufficient. The remaining gap lies in operational efficiency: supplier quality, replenishment scale, pricing, returns, and inventory turnover.

### Failure Taxonomy

1. Coverage failure: the agent does not maintain a broad enough SKU candidate set.
2. Evidence-to-action failure: the agent queries information but does not convert it into correct orders or prices.
3. Supplier-quality failure: the agent does not consistently identify high-quality suppliers.
4. Temporal consistency failure: the agent switches focus without closing the loop on earlier SKU decisions.
5. Constraint-control failure: the agent does not reliably enforce quantity, price, cash, and inventory-risk constraints.
6. Feedback-assimilation failure: returns, reviews, stockouts, and news are not folded into a stable operating policy.

### Improvement Strategies

#### Strategy 1: SKU Candidate Generator

Add a pre-action module that ranks SKUs by demand, stockout risk, margin, inventory age, supplier opportunity, review drift, and news exposure. This module should output a bounded daily candidate set that the LLM must cover before acting.

Expected metric movement: higher daily sold SKUs, higher total sales, fewer low-coverage early failures.

Validation: compare selected runs with and without candidate generation; inspect whether low-coverage models increase from single-digit sold SKU coverage toward 25-30 sold SKUs/day.

#### Strategy 2: Evidence-Gated Action Protocol

Require each state-changing action to pass an evidence checklist. For `place_order`, require inventory, recent sales, supplier quote, quality/return proxy, cash, and delivery timing. For `modify_sku_price`, require current price, cost, recent demand, inventory pressure, and profit signal.

Expected metric movement: higher action correction, lower irrational action rate, lower price distance.

Validation: measure action correction and query depth jointly. The goal is not only higher query depth, but higher conversion from evidence to correct action.

#### Strategy 3: Supplier Quality Memory

Maintain a supplier-SKU score that aggregates returns, reviews, delivery reliability, and historical order outcomes. Use it as a first-class input to procurement.

Expected metric movement: higher raw quality top-1 hit, higher quality ratio, lower return ratio, higher final net worth.

Validation: compare raw quality top-1 hit and return ratio before and after memory integration.

#### Strategy 4: Constraint-Aware Action Validator

Insert a deterministic controller between the LLM and environment mutation tools. It should reject or revise extreme prices, oversized orders, poor supplier choices, and actions missing required evidence.

Expected metric movement: lower action error, lower expiration ratio, fewer cash collapses, lower price distance.

Validation: log rejected actions separately and measure whether blocked actions would have increased expiration, returns, or cash stress.

#### Strategy 5: Hierarchical Operating Policy

Split the agent into daily roles: portfolio planner, SKU analyst, procurement controller, pricing controller, and retrospective monitor. The LLM should not make all decisions in a single unstructured reasoning pass.

Expected metric movement: higher strategy consistency, improved survival, lower variance across scaffolds.

Validation: compare ReAct-style monolithic prompting against hierarchical policy prompting under identical model and environment.

#### Strategy 6: Learning from the Heuristic Without Treating It as a Fair Baseline

Use the heuristic as a source of policy demonstrations for imitation learning, retrieval exemplars, or preference data. The goal is not to claim the heuristic is optimal, but to teach stable operating patterns.

Expected metric movement: improved supplier selection, replenishment rhythm, and inventory turnover.

Validation: train or retrieve from heuristic trajectories, then evaluate whether LLM agents close the gap in final net worth without simply copying privileged hidden-state access.

### Recommended Paper Framing

The strongest paper argument is:

RetailBench reveals that current LLM agents struggle with policy formation under delayed operational feedback. Existing tool-use scaffolds help agents access information, but they do not ensure product-space coverage, supplier-quality tracking, action correction, or temporal consistency. The benchmark therefore exposes a gap between interactive tool use and durable autonomous operation.

The non-LLM heuristic should be framed as an approximate headroom reference. It demonstrates that the environment admits much stronger operating policies, but it should not be presented as a fair model baseline or proven optimum.

### Claims That Are Supported

1. Current selected LLM runs vary widely in survival, sales, SKU coverage, and final net worth.
2. High query depth alone does not imply high action correction.
3. Supplier-quality selection is weak in selected LLM runs.
4. The heuristic achieves much higher final net worth and lower return/expiration ratios.
5. The likely failure mechanism is multi-channel long-horizon compounding.

### Claims That Need More Evidence

1. A specific model is intrinsically better than another model.
2. A specific scaffold is generally superior across models.
3. The heuristic is an upper bound or optimal policy.
4. Any proposed improvement will work without an ablation.
5. Differences are statistically significant.

## Self-Review After Version 2

### Is Version 2 Persuasive?

Yes, substantially more than Version 1. It has a clear central thesis: RetailBench exposes a gap between information access and operational policy formation. It also decomposes failure into mechanistic categories and connects each category to measurable evidence.

### Is Version 2 Data-Supported?

Mostly. It uses survival days, final net worth, total sales, daily sold SKUs, query depth, action correction, raw quality top-1 hit, quality ratio, return ratio, and expired ratio. It avoids unsupported statistical claims and explicitly marks several claims as hypotheses requiring ablation.

The weakest evidence is temporal causality. The report argues that errors compound over time, which is consistent with the simulator and metrics, but it does not yet show day-by-day causal traces for individual SKUs. A top-tier submission would benefit from trajectory figures showing how supplier mistakes or low SKU coverage lead to later returns, stockouts, or cash collapse.

### Does Version 2 Match Top-Conference Standards?

It is close to a strong internal report and a useful paper-analysis draft, but it is not yet a complete top-conference paper section. To meet top-conference standards, it needs:

1. One or two compact figures: e.g., survival vs. sales/SKU coverage, and action correction vs. final net worth.
2. A trajectory case study for one strong and one weak model.
3. Ablations for at least two proposed interventions, ideally SKU candidate generation and supplier-quality memory.
4. Clear distinction between benchmark findings and method contributions if this becomes a full paper.
5. Stronger related-work positioning around tool-use agents, long-horizon decision-making, and business simulation benchmarks.

### Revision Verdict

Version 2 is suitable as a research report and as source material for a paper discussion/analysis section. It should not yet be submitted as final paper text, but the argument is now coherent, evidence-linked, and conservative enough to build on.
