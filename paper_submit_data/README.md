# RetailBench Paper Submit Data

This directory organizes the rollout data used for the `paper/latex_arr_revision`
final evaluation.

## Contents

- `manifest.json`: canonical list of selected runs and their original log paths.
- `raw_runs/`: symlinks to the original run directories or raw non-LLM artifacts.
- `analyze_metrics.py`: metric extraction script aligned with the ARR revision text.
- `analyze_deep_diagnostics.py`: secondary deep-dive script for model/oracle gap analysis.
- `analyze_four_stage_metrics.py`: four-stage behavior analysis covering SKU selection,
  evidence acquisition, action conversion, and temporal follow-up for every run.
- `four_stage_action_metrics.py`: streaming action-conversion diagnostics used by the
  four-stage analyzer to score every LLM run, not just the selected best runs.
- `render_four_stage_report.py`: report renderer that turns `four_stage_metrics.csv`
  into a stage-by-stage report with all-run and survival-first figures.
- `outputs/`: generated CSV/JSON metric summaries, `report.md`, and paper-facing analysis notes.

## Run Selection

The selected package contains seven models:

- Six models have three framework runs: `react`, `reflection`, and `plan_and_act`.
- `GPT-5.5` has one available `react` run.
- `Non-LLM Heuristic` has one `quality_based` hard_v2 run. It is an approximate
  oracle-style reference policy, not a fair LLM-agent baseline.

The raw run directories are linked rather than copied because the full logs are
large and the project volume has limited free space. The symlinks preserve direct
access to `tool_calls.jsonl`, `agent.log`, `environment.log`, `day_*_token_usage.json`,
and per-run JSON summaries for LLM runs. The non-LLM run stores symlinks to
`run_log` and `records_db` under
`raw_runs/non_llm_heuristic/quality_based/run1/`.

## Metrics

Metric groups follow `paper/latex_arr_revision/capter/experiment.tex` and
`paper/latex_arr_revision/table/final_evaluation_summary.tex`:

- Operation: survival days and final net worth.
- Sales: total sales, daily sold SKU coverage, and return ratio.
- Inventory: expired ratio and stockout days.
- Tool use: all/code/memory tool calls per day.
- Token cost: input/output/total tokens and optional estimated cost.

For non-LLM summary-log runs, `analyze_metrics.py` parses the final simulation
summary plus daily `Sales by SKU` and `Insufficient SKUs` lines. Tool and token
cost fields are set to zero because the heuristic policy does not generate
agent tool-call traces or LLM token usage files.

Regenerate the summaries with:

```bash
python3 paper_submit_data/analyze_metrics.py
```

The command writes `outputs/report.md` after each analysis run. The report includes
the metric definitions, run coverage, per-run results, and the default
best-run-per-model selection table. Best-run selection is survival-first:
maximize `run_days`, then use `final_networth` and `total_sales` as tie-breakers.

Optional token prices can be supplied in USD per million tokens:

```bash
python3 paper_submit_data/analyze_metrics.py \
  --input-cost-per-mtok 0 \
  --output-cost-per-mtok 0
```

Generate the deeper diagnostic analysis after `analyze_metrics.py` has run:

```bash
python3 paper_submit_data/analyze_deep_diagnostics.py
```

This writes `outputs/deep_diagnostic_analysis.md` from `run_metrics.csv`,
`best_framework_by_model.csv`, and `selected_llm_action_diagnostics.csv`.

The manuscript-facing failure-analysis report is stored at
`outputs/llm_agent_failure_analysis_report.md`. It contains Version 1, a
self-review, Version 2, and a second self-review against evidence support and
top-conference readiness.

The four-stage measurement framework is stored at
`outputs/four_stage_measurement_framework.md`. It maps SKU selection, evidence
acquisition, action conversion, and temporal follow-up to quantitative metrics,
qualitative diagnostics, and candidate interventions.

Generate the full four-stage run analysis with:

```bash
python3 paper_submit_data/analyze_four_stage_metrics.py
```

This writes `outputs/four_stage_metrics.csv`,
`outputs/four_stage_metrics.json`, and
`outputs/four_stage_analysis_report.md`. The report keeps all 20 runs in the
stage-level tables and applies the survival-first rule only when interpreting
best runs per model. Stage 3 action-conversion diagnostics are recomputed from
each LLM run's `tool_calls.jsonl` and final records database, while the Non-LLM
Heuristic remains included for outcome and follow-up comparison.

Regenerate the stage-organized report and figures from the existing metrics CSV:

```bash
python3 paper_submit_data/render_four_stage_report.py
```

This writes `outputs/four_stage_analysis_report.md`,
`outputs/four_stage_figure_catalog.md`, and eight PNG figures under
`outputs/figures/four_stage/`: one all-run figure and one survival-first
best-run figure for each of the four stages.
