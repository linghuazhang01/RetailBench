#!/usr/bin/env python3
"""Generate a deep diagnostic analysis for RetailBench paper-submit runs."""

from __future__ import annotations

import argparse
import csv
import math
from pathlib import Path
from statistics import mean
from typing import Any


OUTPUT_DIR = Path(__file__).with_name("outputs")


def read_csv(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def as_float(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def fmt(value: Any, digits: int = 4) -> str:
    if value in (None, ""):
        return "--"
    if isinstance(value, str):
        parsed = None
        try:
            parsed = float(value)
        except ValueError:
            return value
        value = parsed
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return "--"
        if abs(value - round(value)) < 1e-9:
            return str(int(round(value)))
        if abs(value) >= 1000:
            return f"{value:,.2f}"
        return f"{value:.{digits}f}"
    return str(value)


def markdown_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> list[str]:
    lines = [
        "| " + " | ".join(title for title, _ in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(fmt(row.get(key)) for _, key in columns) + " |")
    return lines


def correlation(xs: list[float | None], ys: list[float | None]) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 2:
        return None
    mean_x = mean(x for x, _ in pairs)
    mean_y = mean(y for _, y in pairs)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    denom_x = sum((x - mean_x) ** 2 for x, _ in pairs)
    denom_y = sum((y - mean_y) ** 2 for _, y in pairs)
    denominator = math.sqrt(denom_x * denom_y)
    return numerator / denominator if denominator else None


def selected_rows(rows: list[dict[str, str]], best_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    selected_ids = {row["model"]: row["run_id"] for row in best_rows}
    return [row for row in rows if selected_ids.get(row["model"]) == row["run_id"]]


def selected_llm_rows(rows: list[dict[str, str]], best_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    return [row for row in selected_rows(rows, best_rows) if row.get("run_type") == "llm"]


def row_by_model(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    return {row["model"]: row for row in rows}


def range_summary(rows: list[dict[str, str]], key: str) -> dict[str, float | None]:
    values = [value for value in (as_float(row, key) for row in rows) if value is not None]
    if not values:
        return {"min": None, "max": None, "mean": None}
    return {"min": min(values), "max": max(values), "mean": mean(values)}


def metric_correlations(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    days = [as_float(row, "run_days") for row in rows]
    metrics = [
        ("Final Networth", "final_networth"),
        ("Total Sales", "total_sales"),
        ("Sold SKUs/day", "avg_daily_sold_skus"),
        ("Return Ratio", "return_ratio"),
        ("Expired Ratio", "expired_ratio"),
        ("Stockout Ratio", "stockout_ratio"),
        ("Tools/day", "avg_all_tool_calls_per_day"),
    ]
    return [
        {
            "metric": label,
            "corr_with_survival_days": correlation(days, [as_float(row, key) for row in rows]),
        }
        for label, key in metrics
    ]


def action_correlation_rows(action_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    networth = [as_float(row, "final_networth") for row in action_rows]
    days = [as_float(row, "run_days") for row in action_rows]
    metrics = [
        ("Query Depth", "query_depth"),
        ("Action Correction", "action_correction"),
        ("Strategy Consistency", "strategy_consistency"),
        ("Raw Quality Top-1 Hit", "raw_quality_top1_hit"),
        ("Quality Regret", "quality_regret_mean"),
        ("Quality Ratio", "quality_ratio_mean"),
        ("Order Qty / Avg Daily Sales", "order_qty_to_avg_daily_sales_mean"),
        ("Price Distance (%)", "modify_price_distance_pct_mean"),
    ]
    return [
        {
            "metric": label,
            "corr_with_final_networth": correlation(networth, [as_float(row, key) for row in action_rows]),
            "corr_with_survival_days": correlation(days, [as_float(row, key) for row in action_rows]),
        }
        for label, key in metrics
    ]


def oracle_gap_rows(selected_llms: list[dict[str, str]], oracle: dict[str, str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in selected_llms:
        rows.append(
            {
                "model": row["model"],
                "framework": row["framework"],
                "days": as_float(row, "run_days"),
                "days_gap": as_float(oracle, "run_days") - as_float(row, "run_days"),
                "networth_gap": as_float(oracle, "final_networth") - as_float(row, "final_networth"),
                "sales_gap": as_float(oracle, "total_sales") - as_float(row, "total_sales"),
                "return_over_oracle": as_float(row, "return_ratio") - as_float(oracle, "return_ratio"),
                "expired_over_oracle": as_float(row, "expired_ratio") - as_float(oracle, "expired_ratio"),
            }
        )
    return rows


def action_summary_rows(action_rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    columns = [
        "model",
        "framework",
        "query_depth",
        "action_correction",
        "strategy_consistency",
        "raw_quality_top1_hit",
        "quality_regret_mean",
        "quality_ratio_mean",
        "modify_price_distance_pct_mean",
    ]
    return [{key: row.get(key) for key in columns} for row in action_rows]


def render_analysis(
    run_rows: list[dict[str, str]],
    best_rows: list[dict[str, str]],
    action_rows: list[dict[str, str]],
) -> str:
    selected = selected_rows(run_rows, best_rows)
    selected_llms = selected_llm_rows(run_rows, best_rows)
    by_model = row_by_model(selected)
    oracle = by_model["Non-LLM Heuristic"]

    days_summary = range_summary(selected_llms, "run_days")
    networth_summary = range_summary(selected_llms, "final_networth")
    sales_summary = range_summary(selected_llms, "total_sales")
    sku_summary = range_summary(selected_llms, "avg_daily_sold_skus")
    return_summary = range_summary(selected_llms, "return_ratio")
    expired_summary = range_summary(selected_llms, "expired_ratio")

    selected_table_columns = [
        ("Model", "model"),
        ("Framework", "framework"),
        ("Days", "run_days"),
        ("Final Networth", "final_networth"),
        ("Total Sales", "total_sales"),
        ("Sold SKUs/day", "avg_daily_sold_skus"),
        ("Return Ratio", "return_ratio"),
        ("Expired Ratio", "expired_ratio"),
    ]
    oracle_gap_columns = [
        ("Model", "model"),
        ("Framework", "framework"),
        ("Days", "days"),
        ("Days Gap", "days_gap"),
        ("Networth Gap", "networth_gap"),
        ("Sales Gap", "sales_gap"),
        ("Return Over Oracle", "return_over_oracle"),
        ("Expired Over Oracle", "expired_over_oracle"),
    ]
    action_columns = [
        ("Model", "model"),
        ("Framework", "framework"),
        ("QDepth", "query_depth"),
        ("Action Corr", "action_correction"),
        ("Strategy Cons", "strategy_consistency"),
        ("Raw Quality Top1", "raw_quality_top1_hit"),
        ("Quality Regret", "quality_regret_mean"),
        ("Quality Ratio", "quality_ratio_mean"),
        ("Price Dist %", "modify_price_distance_pct_mean"),
    ]
    deepseek_gap = (
        (as_float(oracle, "final_networth") or 0.0)
        - (as_float(by_model["DeepSeek-V4-Pro"], "final_networth") or 0.0)
    )
    gpt_gap = (
        (as_float(oracle, "final_networth") or 0.0)
        - (as_float(by_model["GPT-5.5"], "final_networth") or 0.0)
    )

    lines = [
        "# Deep Diagnostic Analysis",
        "",
        "## Question",
        "",
        "这份分析回答两个问题：",
        "",
        "1. 为什么不同 LLM models / scaffolds 的结果差异这么大？",
        "2. 为什么 selected LLM runs 和 non-LLM oracle-style heuristic 的差距这么大？",
        "",
        "## Evidence and Limits",
        "",
        (
            "分析单位是 survival-first selected run。每个模型只保留 `run_days` 最多的 run，"
            "再用 `final_networth` 和 `total_sales` 作为 tie-break。因此这份分析适合解释当前 "
            "paper-submit selected runs 的行为差异，但不是同 seed 多次重复实验，不能做显著性检验。"
        ),
        "",
        (
            "Action-level diagnostics 来自 `analysis/evaluate_final_metrics.py`，只对 selected LLM runs 计算。"
            "non-LLM heuristic 的 trace-level paper metrics 另见 `selected_action_diagnostics.csv` 和 "
            "`four_stage_metrics.csv`。"
        ),
        "",
        "## Selected Runs",
        "",
        *markdown_table(selected, selected_table_columns),
        "",
        "## Short Answer",
        "",
        (
            "模型间差异大的直接原因不是单一指标，而是 long-horizon compounding："
            "survival、daily SKU coverage、supplier quality selection、price/action correction、"
            "return/expiration loss 会互相放大。一个模型即使某几天动作看起来合理，只要每天少覆盖一批 SKU、"
            "选错一部分 supplier、价格偏离长期需求，后续就会同时损失 sales、cash、reviews 和 inventory space。"
        ),
        "",
        (
            "和 oracle-style heuristic 的差距更大，是因为 heuristic 不是 fair LLM baseline。"
            "它是手写 quality-based policy，显式编码了 shelf assortment、supplier quality、补货节奏和批量采购策略；"
            "LLM agent 则必须通过文本 observation 和工具调用在线推断这些规律，并把 evidence 转成合法动作。"
        ),
        "",
        "## Why Models Differ",
        "",
        "### 1. Survival Separates Stable Operators from Early-Failure Policies",
        "",
        (
            f"Selected LLM survival range 是 {fmt(days_summary['min'])}-{fmt(days_summary['max'])} 天，"
            f"均值 {fmt(days_summary['mean'], 1)} 天。DeepSeek-V4-Pro / plan_and_act 和 GPT-5.5 / react "
            "达到 180 天，Kimi-K2.6 / react 达到 130 天；MiniMax、Qwen、GLM、Grok 的 selected runs "
            "只到 58-73 天。RetailBench 的资金、库存、评论和供应链状态会跨天累积，所以早期小错误会转成后期现金流压力。"
        ),
        "",
        "### 2. SKU Coverage Drives Sales Scale",
        "",
        (
            f"Selected LLM total sales range 是 {fmt(sales_summary['min'])}-{fmt(sales_summary['max'])}，"
            f"daily sold SKU coverage range 是 {fmt(sku_summary['min'])}-{fmt(sku_summary['max'])}。"
            "长 survival runs 通常每天覆盖接近 30 个 sold SKUs；短 horizon runs 常常只有个位数到十几个。"
            "这说明模型差异不只是 supplier 选择，而是每天是否能把足够多的相关 SKU 纳入补货、调价和监控循环。"
        ),
        "",
        "### 3. More Evidence Does Not Guarantee Better Actions",
        "",
        (
            "Action diagnostics 显示 query depth 往往不低，但 action correction 仍偏低。"
            "GPT-5.5 / react 的 query depth 是 0.992，action correction 是 0.444，是 selected LLM 中最强；"
            "DeepSeek-V4-Pro / plan_and_act 的 query depth 是 0.928，但 action correction 只有 0.271；"
            "MiniMax-M2.5 / plan_and_act 的 query depth 是 0.785，action correction 只有 0.176。"
            "这说明主要瓶颈不只是有没有查信息，而是能否把查到的信息稳定转成 supplier、quantity 和 price actions。"
        ),
        "",
        "### 4. Supplier Quality Selection Is a Major Gap",
        "",
        (
            "Raw supplier-quality diagnostics 更直接。selected LLM 的 raw quality top-1 hit 只有约 "
            "0.125-0.346；quality ratio 约 0.543-0.798。也就是说，LLM 经常没有选中隐藏 raw quality 最优的 supplier，"
            "而 supplier quality 会通过 return rate、reviews、future demand 和 cash flow 继续传导。"
        ),
        "",
        "### 5. Tool Use Is Not Monotonic",
        "",
        (
            "Tool calls/day 和 survival 在 selected runs 上有正相关，但它不是充分条件。"
            "Grok-4.3 / react 的 tools/day 低、query depth 也低，sales coverage 明显不足；"
            "但高工具使用也不必然产生高 performance，因为 action correction 和 supplier quality selection 才决定工具信息是否变成有效策略。"
        ),
        "",
        "## Descriptive Correlations",
        "",
        "这些相关性只基于 7 个 selected LLM runs，用来辅助解释模式，不用于显著性判断。",
        "",
        *markdown_table(metric_correlations(selected_llms), [("Metric", "metric"), ("Corr with Survival Days", "corr_with_survival_days")]),
        "",
        "Action-level correlations 同样是 descriptive。这里 action correction 与 final networth 的相关性最高，说明动作转化质量比单纯 query depth 更接近最终经营结果。",
        "",
        *markdown_table(action_correlation_rows(action_rows), [
            ("Metric", "metric"),
            ("Corr with Final Networth", "corr_with_final_networth"),
            ("Corr with Survival Days", "corr_with_survival_days"),
        ]),
        "",
        "## Action-Level Diagnostics",
        "",
        *markdown_table(action_summary_rows(action_rows), action_columns),
        "",
        "## Why the Gap to Oracle-Style Heuristic Is Large",
        "",
        "### 1. The Heuristic Encodes a Stable Operating Policy",
        "",
        (
            "Non-LLM heuristic 的 final networth 是 "
            f"{fmt(as_float(oracle, 'final_networth'))}，total sales 是 {fmt(as_float(oracle, 'total_sales'))}，"
            f"return ratio 是 {fmt(as_float(oracle, 'return_ratio'))}，expired ratio 是 {fmt(as_float(oracle, 'expired_ratio'))}。"
            "它直接编码 quality-based procurement、补货强度和 shelf assortment，不需要从文本历史里在线归纳策略。"
        ),
        "",
        "### 2. Equal Survival Does Not Mean Equal Operations",
        "",
        (
            "DeepSeek-V4-Pro / plan_and_act 和 GPT-5.5 / react 都能活到 180 天，"
            f"但 final networth 仍分别低 heuristic {deepseek_gap:,.2f} 和 {gpt_gap:,.2f}。"
            "这说明 LLM 的主要差距不只是能否活到最后，还包括利润率、supplier quality、价格设定和库存周转效率。"
        ),
        "",
        "### 3. Lower Stockout Ratio Can Be Misleading",
        "",
        (
            "Heuristic 的 stockout ratio 很高，但它同时有最高 total sales 和最高 networth。"
            "这意味着它可能在高吞吐、lean inventory 下承受 stockout；相反，一些 LLM runs 的 stockout ratio 较低，"
            "可能只是因为 SKU coverage 和 demand activation 太弱，不能直接解释为库存管理更好。"
        ),
        "",
        "### 4. Quality Loss Compounds Over Time",
        "",
        (
            f"Selected LLM return ratio range 是 {fmt(return_summary['min'])}-{fmt(return_summary['max'])}，"
            f"expired ratio range 是 {fmt(expired_summary['min'])}-{fmt(expired_summary['max'])}，"
            "均高于 heuristic 的 0.0201 return ratio 和 0.0024 expired ratio。"
            "在 RetailBench 中，这些不是一次性损失：returns 会影响 revenue 和 reviews，expiration 会占用采购资金和库存空间，"
            "supplier quality 会影响后续 demand。"
        ),
        "",
        "## Gap to Heuristic",
        "",
        "Gap 定义：`heuristic - selected LLM`；return/expired gap 定义为 `selected LLM - heuristic`。",
        "",
        *markdown_table(oracle_gap_rows(selected_llms, oracle), oracle_gap_columns),
        "",
        "## Paper-Level Interpretation",
        "",
        (
            "可以在论文中保守表述为：RetailBench 区分了 short-term action execution 和 long-horizon operational competence。"
            "当前 LLM agents 的差异主要来自能否持续覆盖足够多 SKU、把 evidence 转换为稳定动作、并控制 supplier quality 带来的 returns/expiration loss。"
            "Non-LLM heuristic 的作用是显示环境仍有明显 headroom，而不是作为公平模型 baseline 或严格 upper bound。"
        ),
        "",
        "不建议写成：oracle 证明了最优上界、某模型显著优于另一模型、或差异完全来自模型能力。当前数据更支持 descriptive diagnosis 和 headroom framing。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Write deep diagnostic analysis for paper-submit data.")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    run_rows = read_csv(args.output_dir / "run_metrics.csv")
    best_rows = read_csv(args.output_dir / "best_framework_by_model.csv")
    action_rows = read_csv(args.output_dir / "selected_llm_action_diagnostics.csv")
    if not run_rows or not best_rows:
        raise SystemExit("run_metrics.csv and best_framework_by_model.csv are required")
    if not action_rows:
        raise SystemExit("selected_llm_action_diagnostics.csv is required")

    text = render_analysis(run_rows, best_rows, action_rows)
    output_path = args.output_dir / "deep_diagnostic_analysis.md"
    output_path.write_text(text, encoding="utf-8")
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
