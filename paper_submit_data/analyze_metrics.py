#!/usr/bin/env python3
"""Analyze RetailBench paper-submit rollouts under the ARR revision metric groups.

Metric grouping follows:
- paper/latex_arr_revision/capter/experiment.tex
- paper/latex_arr_revision/table/final_evaluation_summary.tex

The script reads manifest.json, evaluates each listed run, and writes:
- outputs/run_metrics.csv
- outputs/run_metrics.json
- outputs/best_framework_by_model.csv
- outputs/metric_definitions.md
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analysis.evaluate_final_metrics import analyze_tool_calls, iter_tool_call_days  # noqa: E402


METRIC_DEFINITIONS: dict[str, dict[str, str]] = {
    "operation": {
        "run_days": "episode 终止或分析窗口结束前的经营天数，越高越好。",
        "final_networth": "最后一个分析日的 net worth，越高越好。",
    },
    "sales": {
        "total_sales": "分析窗口内累计售出件数，由每日 sales_by_sku 汇总得到，越高越好。",
        "avg_daily_sold_skus": "平均每天实际发生销售的不同 SKU 数量，越高表示销售覆盖更广。",
        "return_ratio": "退货件数除以售出件数，越低越好。",
    },
    "inventory": {
        "expired_ratio": "过期件数除以售出件数与过期件数之和，越低越好。",
        "stockout_days": "insufficient_skus 非空的天数，越低越好。",
        "stockout_ratio": "缺货天数除以分析天数，越低越好。",
    },
    "tool_use": {
        "avg_direct_tool_calls_per_day": "平均每天顶层工具调用次数，不包含 execute_code 内部触发的工具调用。",
        "avg_all_tool_calls_per_day": "平均每天总工具调用次数，包含顶层工具调用与 execute_code 内部触发的工具调用。",
    },
    "token_cost": {
        "avg_tokens_per_day": "total_tokens 除以有 token usage 记录的天数。",
        "avg_cost_usd_per_day": "可选的每日平均费用估计；未提供 token 单价时为空。",
    },
}

HIGHER_IS_BETTER = {
    "run_days",
    "final_networth",
    "total_sales",
    "avg_daily_sold_skus",
    "avg_direct_tool_calls_per_day",
    "avg_all_tool_calls_per_day",
}

LOWER_IS_BETTER = {
    "return_ratio",
    "expired_ratio",
    "stockout_days",
    "stockout_ratio",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "avg_tokens_per_day",
    "avg_cost_usd_per_day",
}

BEST_RUN_METRIC_KEYS = [
    "run_type",
    "run_days",
    "final_networth",
    "total_sales",
    "avg_daily_sold_skus",
    "return_ratio",
    "expired_ratio",
    "stockout_days",
    "stockout_ratio",
    "avg_direct_tool_calls_per_day",
    "avg_all_tool_calls_per_day",
    "avg_tokens_per_day",
    "avg_cost_usd_per_day",
]

MEMORY_TOOLS = {"view_notes", "add_note"}
INTEGER_SUMMARY_KEYS = {"run_days", "total_sold", "total_ordered", "total_expired", "total_returns"}


@dataclass(frozen=True)
class RunSpec:
    model: str
    model_slug: str
    framework: str
    run_id: str
    source_path: Path
    run_type: str
    source_format: str
    database_path: Path | None
    notes: str | None


def load_manifest(path: Path) -> tuple[dict[str, Any], list[RunSpec]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    runs = [
        RunSpec(
            model=item["model"],
            model_slug=item["model_slug"],
            framework=item["framework"],
            run_id=item["run_id"],
            source_path=resolve_manifest_path(item["source_path"]),
            run_type=item.get("run_type", "llm"),
            source_format=item.get("source_format", "tool_calls"),
            database_path=(
                resolve_manifest_path(item["database_path"])
                if item.get("database_path")
                else None
            ),
            notes=item.get("notes"),
        )
        for item in payload["runs"]
    ]
    return payload, runs


def resolve_manifest_path(raw_path: str) -> Path:
    path = Path(raw_path)
    return path if path.is_absolute() else REPO_ROOT / path


def finite_or_none(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def summarize_token_usage(run_dir: Path, input_cost_per_mtok: float, output_cost_per_mtok: float) -> dict[str, Any]:
    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    cached_prompt_tokens = 0
    token_days = 0

    for path in sorted(run_dir.glob("day_*_token_usage.json")):
        try:
            item = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        token_days += 1
        input_tokens += int(item.get("prompt_tokens") or 0)
        output_tokens += int(item.get("completion_tokens") or 0)
        total_tokens += int(item.get("total_tokens") or 0)
        cached_prompt_tokens += int(item.get("cached_prompt_tokens") or 0)

    cost = None
    if token_days and (input_cost_per_mtok > 0 or output_cost_per_mtok > 0):
        cost = (
            input_tokens / 1_000_000 * input_cost_per_mtok
            + output_tokens / 1_000_000 * output_cost_per_mtok
        )

    return {
        "token_days": token_days,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "cached_prompt_tokens": cached_prompt_tokens,
        "avg_tokens_per_day": total_tokens / token_days if token_days else None,
        "estimated_cost_usd": cost,
        "avg_cost_usd_per_day": cost / token_days if cost is not None and token_days else None,
    }


def summarize_tool_use(run_dir: Path, max_days: int | None) -> dict[str, Any]:
    days = iter_tool_call_days(run_dir / "tool_calls.jsonl", max_days=max_days)
    analyzed_days = [day for day in days if day.get("end_today")]
    denominator = len(analyzed_days) or len(days)

    all_calls = 0
    direct_calls = 0
    code_calls = 0
    memory_calls = 0
    for day in days:
        direct_records = day.get("direct_records", [])
        code_records = day.get("code_internal_records", [])
        all_records = direct_records + code_records
        direct_calls += sum(1 for item in direct_records if item.get("tool"))
        all_calls += sum(1 for item in all_records if item.get("tool"))
        code_calls += sum(1 for item in code_records if item.get("tool"))
        memory_calls += sum(1 for item in all_records if item.get("tool") in MEMORY_TOOLS)

    return {
        "tool_days": denominator,
        "direct_tool_calls": direct_calls,
        "all_tool_calls": all_calls,
        "code_tool_calls": code_calls,
        "memory_tool_calls": memory_calls,
        "avg_direct_tool_calls_per_day": direct_calls / denominator if denominator else None,
        "avg_all_tool_calls_per_day": all_calls / denominator if denominator else None,
        "avg_code_tool_calls_per_day": code_calls / denominator if denominator else None,
        "avg_memory_tool_calls_per_day": memory_calls / denominator if denominator else None,
    }


def parse_number(raw_value: str) -> float:
    return float(raw_value.replace(",", ""))


def count_sold_skus(line: str) -> int:
    _, payload = line.split("Sales by SKU:", 1)
    count = 0
    for item in payload.split(","):
        if "=" not in item:
            continue
        _, raw_qty = item.rsplit("=", 1)
        try:
            quantity = float(raw_qty.strip())
        except ValueError:
            continue
        if quantity > 0:
            count += 1
    return count


def has_stockout(line: str) -> bool:
    _, payload = line.split("Insufficient SKUs:", 1)
    value = payload.strip().lower()
    return bool(value and value not in {"none", "no", "n/a", "na", "[]"})


def find_summary_log(source_path: Path) -> Path:
    if source_path.is_file():
        return source_path
    for name in ("run_log", "run.log", "simulation.log", "non_llm.log"):
        candidate = source_path / name
        if candidate.exists():
            return candidate
    logs = sorted(source_path.glob("*.log")) if source_path.exists() else []
    if len(logs) == 1:
        return logs[0]
    return source_path / "run.log"


def analyze_summary_log(source_path: Path) -> dict[str, Any]:
    log_path = find_summary_log(source_path)
    metrics: dict[str, Any] = {
        "run_days": None,
        "final_net_worth": None,
        "total_sold": None,
        "return_ratio": None,
        "expired_ratio": None,
    }
    sold_sku_counts: list[int] = []
    stockout_days = 0

    patterns = {
        "run_days": re.compile(r"Run Days:\s*([0-9,]+)"),
        "final_net_worth": re.compile(r"Final Net Worth:\s*([0-9,.\-]+)"),
        "final_funds": re.compile(r"Final Funds:\s*([0-9,.\-]+)"),
        "avg_daily_sold_units": re.compile(r"Avg Daily Sold:\s*([0-9,.\-]+)"),
        "avg_daily_profit": re.compile(r"Avg Daily Profit:\s*([0-9,.\-]+)"),
        "total_sold": re.compile(r"Total Sold:\s*([0-9,.\-]+)"),
        "total_ordered": re.compile(r"Total Ordered:\s*([0-9,.\-]+)"),
        "total_expired": re.compile(r"Total Expired:\s*([0-9,.\-]+)"),
        "total_returns": re.compile(r"Total Returns:\s*([0-9,.\-]+)"),
        "expired_ratio": re.compile(r"Expired Ratio:\s*([0-9,.\-]+)"),
        "return_ratio": re.compile(r"Return Ratio:\s*([0-9,.\-]+)"),
    }

    try:
        with log_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if "Sales by SKU:" in line:
                    sold_sku_counts.append(count_sold_skus(line))
                elif "Insufficient SKUs:" in line and has_stockout(line):
                    stockout_days += 1

                for key, pattern in patterns.items():
                    match = pattern.search(line)
                    if match:
                        value = parse_number(match.group(1))
                        metrics[key] = int(value) if key in INTEGER_SUMMARY_KEYS else value
    except OSError:
        return metrics

    run_days_value = metrics.get("run_days")
    run_days = int(run_days_value) if isinstance(run_days_value, (int, float)) else len(sold_sku_counts)
    if sold_sku_counts:
        denominator = run_days if run_days else len(sold_sku_counts)
        metrics["avg_daily_sold_skus"] = sum(sold_sku_counts) / denominator
    metrics["stockout_days"] = stockout_days
    metrics["stockout_ratio"] = stockout_days / run_days if run_days else None
    metrics["summary_log_path"] = str(log_path)
    return metrics


def empty_tool_and_token_usage(run_days: Any) -> dict[str, Any]:
    days = int(run_days) if isinstance(run_days, (int, float)) and run_days else 0
    return {
        "tool_days": days,
        "direct_tool_calls": 0,
        "all_tool_calls": 0,
        "code_tool_calls": 0,
        "memory_tool_calls": 0,
        "avg_direct_tool_calls_per_day": 0.0,
        "avg_all_tool_calls_per_day": 0.0,
        "avg_code_tool_calls_per_day": 0.0,
        "avg_memory_tool_calls_per_day": 0.0,
        "token_days": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cached_prompt_tokens": 0,
        "avg_tokens_per_day": 0.0,
        "estimated_cost_usd": 0.0,
        "avg_cost_usd_per_day": 0.0,
    }


def analyze_run(spec: RunSpec, max_days: int | None, input_cost_per_mtok: float, output_cost_per_mtok: float) -> dict[str, Any]:
    run_dir = spec.source_path
    if spec.source_format == "summary_log":
        performance = analyze_summary_log(run_dir)
        tool_use = empty_tool_and_token_usage(performance.get("run_days"))
        token_usage = {}
    else:
        performance = analyze_tool_calls(run_dir / "tool_calls.jsonl", max_days=max_days) or {}
        tool_use = summarize_tool_use(run_dir, max_days=max_days)
        token_usage = summarize_token_usage(run_dir, input_cost_per_mtok, output_cost_per_mtok)

    row = {
        "model": spec.model,
        "model_slug": spec.model_slug,
        "framework": spec.framework,
        "run_id": spec.run_id,
        "run_type": spec.run_type,
        "source_format": spec.source_format,
        "source_path": str(spec.source_path),
        "database_path": str(spec.database_path) if spec.database_path else None,
        "source_exists": spec.source_path.exists(),
        "has_done_marker": (spec.source_path / ".done").exists(),
        "notes": spec.notes,
        "run_days": performance.get("run_days"),
        "final_networth": performance.get("final_net_worth"),
        "total_sales": performance.get("total_sold"),
        "avg_daily_sold_skus": performance.get("avg_daily_sold_skus"),
        "return_ratio": performance.get("return_ratio"),
        "expired_ratio": performance.get("expired_ratio"),
        "stockout_days": performance.get("stockout_days"),
        "stockout_ratio": performance.get("stockout_ratio"),
    }
    row.update(tool_use)
    row.update(token_usage)
    return {key: finite_or_none(value) if isinstance(value, (int, float)) else value for key, value in row.items()}


def metric_value(row: dict[str, Any], metric: str) -> float:
    value = row.get(metric)
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return -math.inf if metric in HIGHER_IS_BETTER else math.inf


def choose_best_run(items: list[dict[str, Any]]) -> dict[str, Any]:
    return sorted(
        items,
        key=lambda item: (
            metric_value(item, "run_days"),
            metric_value(item, "final_networth"),
            metric_value(item, "total_sales"),
        ),
        reverse=True,
    )[0]


def choose_best_framework(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["model"]), []).append(row)

    selected = []
    for model, items in grouped.items():
        best = choose_best_run(items)
        row = {
            "model": model,
            "selected_framework": best["framework"],
            "run_id": best["run_id"],
        }
        row.update({key: best.get(key) for key in BEST_RUN_METRIC_KEYS})
        row["selection_rule"] = "max run_days, tie-break by final_networth then total_sales"
        selected.append(row)
    return sorted(selected, key=lambda item: item["model"])


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_definitions(path: Path) -> None:
    lines = [
        "# Metric Definitions",
        "",
        "Definitions follow `paper/latex_arr_revision/capter/experiment.tex` and "
        "`paper/latex_arr_revision/table/final_evaluation_summary.tex`.",
        "",
    ]
    for group, metrics in METRIC_DEFINITIONS.items():
        lines.append(f"## {group}")
        for name, definition in metrics.items():
            direction = "越高越好" if name in HIGHER_IS_BETTER else "越低越好"
            if name == "avg_cost_usd_per_day":
                direction = "提供 token 单价时越低越好"
            lines.append(f"- `{name}` ({direction}): {definition}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def format_value(value: Any, digits: int = 4) -> str:
    if value is None:
        return "--"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if not math.isfinite(value):
            return "--"
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
        lines.append("| " + " | ".join(format_value(row.get(key)) for _, key in columns) + " |")
    return lines


def framework_coverage(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["model"]), []).append(row)

    coverage = []
    for model, items in sorted(grouped.items()):
        frameworks = sorted(str(item["framework"]) for item in items)
        run_types = sorted({str(item.get("run_type", "llm")) for item in items})
        coverage.append(
            {
                "model": model,
                "run_type": ", ".join(run_types),
                "run_count": len(items),
                "frameworks": ", ".join(frameworks),
                "complete_three_framework": "yes" if len(frameworks) == 3 else "no",
            }
        )
    return coverage


def selected_runs_by_model(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["model"]), []).append(row)
    return {model: choose_best_run(items) for model, items in grouped.items()}


def numeric_delta(left: dict[str, Any], right: dict[str, Any], metric: str) -> float | None:
    left_value = left.get(metric)
    right_value = right.get(metric)
    if isinstance(left_value, (int, float)) and isinstance(right_value, (int, float)):
        return float(left_value) - float(right_value)
    return None


def oracle_gap_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    selected = selected_runs_by_model(rows)
    oracle = selected.get("Non-LLM Heuristic")
    if oracle is None:
        return []

    gap_rows: list[dict[str, Any]] = []
    for model, row in sorted(selected.items()):
        if row.get("run_type") != "llm":
            continue
        gap_rows.append(
            {
                "model": model,
                "selected_framework": row.get("framework"),
                "days": row.get("run_days"),
                "days_gap": numeric_delta(oracle, row, "run_days"),
                "networth_gap": numeric_delta(oracle, row, "final_networth"),
                "sales_gap": numeric_delta(oracle, row, "total_sales"),
                "return_ratio_over_oracle": numeric_delta(row, oracle, "return_ratio"),
                "expired_ratio_over_oracle": numeric_delta(row, oracle, "expired_ratio"),
                "tools_per_day": row.get("avg_all_tool_calls_per_day"),
            }
        )
    return gap_rows


def mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    return None


def diagnostic_analysis_lines(rows: list[dict[str, Any]]) -> list[str]:
    selected = selected_runs_by_model(rows)
    selected_llms = [row for row in selected.values() if row.get("run_type") == "llm"]
    oracle = selected.get("Non-LLM Heuristic")
    if not selected_llms or oracle is None:
        return []

    days_values = [value for value in (as_float(row.get("run_days")) for row in selected_llms) if value is not None]
    networth_values = [
        value for value in (as_float(row.get("final_networth")) for row in selected_llms) if value is not None
    ]
    tool_values = [
        value for value in (as_float(row.get("avg_all_tool_calls_per_day")) for row in selected_llms) if value is not None
    ]
    best_llm_networth = max(networth_values) if networth_values else None
    best_llm_sales = max(
        value for value in (as_float(row.get("total_sales")) for row in selected_llms) if value is not None
    )
    oracle_networth = as_float(oracle.get("final_networth"))
    oracle_sales = as_float(oracle.get("total_sales"))
    oracle_return = as_float(oracle.get("return_ratio"))
    oracle_expired = as_float(oracle.get("expired_ratio"))

    gap_columns = [
        ("Model", "model"),
        ("Selected", "selected_framework"),
        ("Days", "days"),
        ("Days Gap", "days_gap"),
        ("Networth Gap", "networth_gap"),
        ("Sales Gap", "sales_gap"),
        ("Return Ratio Over Oracle", "return_ratio_over_oracle"),
        ("Expired Ratio Over Oracle", "expired_ratio_over_oracle"),
        ("Tools/day", "tools_per_day"),
    ]

    lines = [
        "## Diagnostic Analysis",
        "",
        "### Analysis Limits",
        "",
        (
            "这里的分析是 descriptive analysis：unit 是 selected rollout run，"
            "不是严格同 seed 的多次重复实验。因此它能解释当前数据中的主要差异模式，"
            "但不支持显著性检验或把差异完全归因到某一个模型能力维度。"
        ),
        "",
        "### Why Models Differ",
        "",
        (
            f"Survival days 是最强的第一层分化信号。按 survival-first 选择后，"
            f"LLM selected runs 的 survival range 是 {int(min(days_values))}-{int(max(days_values))} 天，"
            f"均值约 {mean(days_values):.1f} 天。DeepSeek-V4-Pro / plan_and_act 和 GPT-5.5 / react "
            "都达到 180 天；Kimi-K2.6 / react 达到 130 天；其余 selected runs 只到 58-73 天。"
            "这说明很多差异首先来自是否能长期维持现金流和库存周转，而不只是某一天的动作质量。"
        ),
        "",
        (
            "第二层差异来自 SKU coverage 和销售规模。能长期运行的 selected LLM runs "
            "通常每天覆盖约 30 个 sold SKUs，并达到 136k-164k total sales；"
            "短 horizon runs 往往只有个位数到十几个 sold SKUs/day，total sales 也低一个数量级。"
            "这表明模型不是只在单个 supplier 或 price action 上有差异，而是在每天能否持续选择足够多、"
            "足够相关的 SKU 进入补货和调价集合上有明显差异。"
        ),
        "",
        (
            "第三层差异来自 loss channels。selected LLM runs 的 return ratio 明显高于 heuristic baseline，"
            "并且部分 runs 有较高 expired ratio；这些损失会在 long-horizon setting 中复利式影响资金、"
            "库存空间和后续采购能力。stockout ratio 需要和 sales 一起读：低 stockout 不一定代表更好，"
            "也可能只是因为模型销售覆盖太窄、触发的需求不足。"
        ),
        "",
        (
            f"Tool use 也不是单调收益信号。selected LLM runs 的 tools/day 范围约 "
            f"{min(tool_values):.1f}-{max(tool_values):.1f}。低工具调用的 Grok-4.3 / react 覆盖和销量偏低；"
            "但高工具调用的 runs 也不必然更好。差异更像是 evidence selection 和 action translation 的质量差异，"
            "而不是简单的工具调用次数差异。"
        ),
        "",
        "### Why LLM Runs Differ from the Oracle-Style Heuristic",
        "",
        (
            "Non-LLM Heuristic 是手写 quality-based policy，用作 approximate oracle-style reference，"
            "不是 fair LLM baseline。它没有自然语言推理、上下文压缩、工具选择、JSON/action 格式、"
            "token budget 或每日候选 SKU 选择的不确定性；同时它显式编码了 supplier quality、补货节奏、"
            "shelf assortment 和批量采购策略。"
        ),
        "",
        (
            f"差距不只是 survival。即使 DeepSeek-V4-Pro / plan_and_act 和 GPT-5.5 / react 都活到 180 天，"
            f"它们的 final networth 仍分别低于 heuristic {oracle_networth - as_float(selected['DeepSeek-V4-Pro']['final_networth']):,.2f} "
            f"和 {oracle_networth - as_float(selected['GPT-5.5']['final_networth']):,.2f}；"
            f"best LLM networth 是 {best_llm_networth:,.2f}，仍显著低于 heuristic 的 {oracle_networth:,.2f}。"
            f"total sales 也类似：best selected LLM sales 是 {best_llm_sales:,.0f}，"
            f"低于 heuristic 的 {oracle_sales:,.0f}。"
        ),
        "",
        (
            f"更直接的 operational gap 是质量控制。heuristic 的 return ratio 是 {oracle_return:.4f}，"
            f"expired ratio 是 {oracle_expired:.4f}；selected LLM runs 普遍高于这个水平。"
            "这和 paper 中的环境设定一致：supplier choice 会同时影响 procurement cost、quality、reviews、"
            "return pressure 和未来需求。LLM agent 即使能看到一部分 evidence，也需要持续把 supplier quality、"
            "price、inventory age、cash constraint 和 demand shocks 组合成稳定 policy；这正是 heuristic 手写策略的优势。"
        ),
        "",
        "### Selected LLM Gap to Heuristic",
        "",
        "Gap 定义：`Days/Networth/Sales Gap = heuristic - selected LLM`；"
        "`Return/Expired Ratio Over Oracle = selected LLM - heuristic`，正数表示高于 heuristic。",
        "",
        *markdown_table(oracle_gap_rows(rows), gap_columns),
        "",
    ]
    return lines


def format_percent(value: Any) -> str:
    number = as_float(value)
    if number is None:
        return "--"
    return f"{number * 100:.1f}%"


def stage3_supplier_diagnostic_lines(output_dir: Path) -> list[str]:
    diagnostic_path = output_dir / "supplier_quality_failure_cases.json"
    if not diagnostic_path.exists():
        return []

    payload = json.loads(diagnostic_path.read_text(encoding="utf-8"))
    summary = payload.get("summary", {})
    all_llm = summary.get("all_llm", {})
    best_llm = summary.get("survival_best_llm", {})
    sample_n = payload.get("max_sample_lines_per_run", "--")
    first_order_path = output_dir / "supplier_rank_history_first_order_analysis.json"
    first_order_line = ""
    if first_order_path.exists():
        first_payload = json.loads(first_order_path.read_text(encoding="utf-8"))
        history = first_payload.get("history_summary", {})
        first_summary = first_payload.get("first_order_summary", {})
        first_all = first_summary.get("all_llm_first_orders", {})
        first_best = first_summary.get("survival_best_first_orders", {})
        first_order_line = (
            "- 新增 first-order 诊断进一步说明：SQL 历史数据本身明显偏向高质量 supplier，"
            f"unit-weighted raw quality rank 为 {format_value(history.get('unit_weighted_quality_rank_mean'), digits=3)}，"
            f"QualityFirst 为 {format_percent(history.get('unit_weighted_quality_first_rate'))}，"
            f"PriceFirst 只有 {format_percent(history.get('unit_weighted_price_first_rate'))}；"
            f"但全部 LLM runs 对每个 SKU 第一次成功下单时，mean quality rank 为 "
            f"{format_value(first_all.get('quality_rank_mean'), digits=3)}，"
            f"QualityFirst 只有 {format_percent(first_all.get('quality_first_rate'))}，"
            f"PriceFirst 为 {format_percent(first_all.get('price_first_rate'))}。"
            f"Survival-best runs 也类似：first-order mean quality rank 为 "
            f"{format_value(first_best.get('quality_rank_mean'), digits=3)}，"
            f"QualityFirst {format_percent(first_best.get('quality_first_rate'))}，"
            f"PriceFirst {format_percent(first_best.get('price_first_rate'))}。"
            "这说明模型不是后期 drift 后才偏离质量结构，而是在第一次 procurement decision 时就没有稳定识别 supplier quality rank。"
        )

    lines = [
        "## Stage 3 Supplier Quality Diagnostic",
        "",
        (
            "这一段把四阶段报告中的 Stage 3 结论同步到主 `report.md`。"
            "核心问题不是模型完全不会查询 supplier 信息，而是 supplier 选择被低价信号系统性主导。"
        ),
        "",
        (
            "- Non-LLM Heuristic 的 trace-level reference 指标按当前校正口径记录为："
            "`QDepth = 1.0000`，`Price Depth = 1.0000`，`ActionCorr = 0.8863`。"
            "这里的 `QDepth` 和 `Price Depth` 使用 heuristic policy 的 fully specified decision rule 口径，"
            "而不是 LLM trace 中的自然语言查询覆盖率。"
        ),
        (
            f"- 全量 LLM runs 的 `QualityFirst%` 为 {format_percent(all_llm.get('quality_first_rate'))}，"
            f"`PriceFirst%` 为 {format_percent(all_llm.get('price_first_rate'))}；"
            f"survival-best runs 的 `QualityFirst%` 为 {format_percent(best_llm.get('quality_first_rate'))}，"
            f"`PriceFirst%` 为 {format_percent(best_llm.get('price_first_rate'))}。"
        ),
        (
            f"- 日志审计样本（每个 survival-best run 最多 {sample_n} 条成功下单 line）显示，"
            f"下单前 supplier price query 覆盖率为 {format_percent(best_llm.get('supplier_price_query_rate'))}，"
            f"但 supplier return/rating 等 quality proxy query 覆盖率只有 "
            f"{format_percent(best_llm.get('quality_proxy_query_rate'))}。"
        ),
        (
            f"- 在未选中 raw quality 最优 supplier 的 order lines 中，"
            f"{format_percent(best_llm.get('non_quality_first_price_first_rate'))} 仍然选择了最低价 supplier。"
            "这支持“信息呈现不足 + action conversion 不足”的解释：价格最显眼、最容易比较，"
            "而质量需要跨 supplier return/rating、review、历史 outcome 等 proxy 做组合推断。"
        ),
    ]
    if first_order_line:
        lines.append(first_order_line)
    lines.extend([
        (
            "- 写作时应把 `QualityFirst%` 表述为 hidden/raw quality diagnostic，而不是模型当时完全可见信息下的公平正确率；"
            "更稳妥的 claim 是：当前 LLM agent 没有形成 `supplier candidate table -> quality-adjusted ranking -> place_order` 的稳定闭环。"
        ),
        "",
    ])
    return lines


def metric_report_lines() -> list[str]:
    lines = [
        "## Metrics",
        "",
        "指标分组来自 `paper/latex_arr_revision/capter/experiment.tex` 和 "
        "`paper/latex_arr_revision/table/final_evaluation_summary.tex`。",
        "",
    ]
    for group, metrics in METRIC_DEFINITIONS.items():
        title = {
            "operation": "Operation",
            "sales": "Sales",
            "inventory": "Inventory",
            "tool_use": "Tool Use",
            "token_cost": "Token Cost",
        }.get(group, group.replace("_", " ").title())
        lines.append(f"### {title}")
        lines.append("")
        lines.extend(markdown_table(
            [
                {
                    "metric": name,
                    "direction": (
                        "越高越好"
                        if name in HIGHER_IS_BETTER
                        else "越低越好"
                    ),
                    "definition": definition,
                }
                for name, definition in metrics.items()
            ],
            [
                ("Metric", "metric"),
                ("方向", "direction"),
                ("定义", "definition"),
            ],
        ))
        lines.append("")
    return lines


def write_report(path: Path, rows: list[dict[str, Any]], best_rows: list[dict[str, Any]]) -> None:
    coverage_rows = framework_coverage(rows)
    complete_models = sum(1 for row in coverage_rows if row["complete_three_framework"] == "yes")

    overview_columns = [
        ("Model", "model"),
        ("Type", "run_type"),
        ("Runs", "run_count"),
        ("Frameworks", "frameworks"),
        ("3-framework complete", "complete_three_framework"),
    ]
    run_columns = [
        ("Model", "model"),
        ("Type", "run_type"),
        ("Framework", "framework"),
        ("Days", "run_days"),
        ("Final Networth", "final_networth"),
        ("Total Sales", "total_sales"),
        ("Sold SKUs/day", "avg_daily_sold_skus"),
        ("Return Ratio", "return_ratio"),
        ("Expired Ratio", "expired_ratio"),
        ("Stockout Days", "stockout_days"),
        ("Direct Tools/day", "avg_direct_tool_calls_per_day"),
        ("Total Tools/day", "avg_all_tool_calls_per_day"),
        ("Avg Tokens/day", "avg_tokens_per_day"),
        ("Avg Cost/day", "avg_cost_usd_per_day"),
    ]
    best_columns = [
        ("Model", "model"),
        ("Type", "run_type"),
        ("Selected Framework", "selected_framework"),
        ("Run ID", "run_id"),
        ("Days", "run_days"),
        ("Final Networth", "final_networth"),
        ("Total Sales", "total_sales"),
        ("Sold SKUs/day", "avg_daily_sold_skus"),
        ("Return Ratio", "return_ratio"),
        ("Expired Ratio", "expired_ratio"),
        ("Stockout Days", "stockout_days"),
        ("Stockout Ratio", "stockout_ratio"),
        ("Direct Tools/day", "avg_direct_tool_calls_per_day"),
        ("Total Tools/day", "avg_all_tool_calls_per_day"),
        ("Avg Tokens/day", "avg_tokens_per_day"),
        ("Avg Cost/day", "avg_cost_usd_per_day"),
        ("Rule", "selection_rule"),
    ]

    lines = [
        "# RetailBench Paper Submit Data Report",
        "",
        "## Scope",
        "",
        (
            f"本报告汇总 {len(coverage_rows)} 个模型/基线的 {len(rows)} 个 selected rollout runs。"
            f"其中 {complete_models} 个模型包含 `react`、`reflection`、`plan_and_act` "
            "三个 framework run；剩余条目使用 `manifest.json` 中当前可用的 run。"
        ),
        "",
        "原始 run 路径由 `manifest.json` 显式声明。LLM 条目通常指向 `/Volumes/Data` "
        "原始日志目录；non-LLM 条目既支持 legacy `run_log`/`records_db` summary 输入，"
        "也支持包含 `tool_calls.jsonl` 与 `db/records.db` 的完整 run 目录。",
        "",
        "当 non-LLM heuristic baseline 提供 `tool_calls.jsonl` 时，脚本从 trace 中解析经营指标和"
        " tool-use 指标；summary-only 输入则从 summary log 中解析经营指标，并将 tool/token cost 记为 0。"
        "non-LLM 不产生 LLM token usage，因此 token/cost 字段保持为空或 0。",
        "",
        *metric_report_lines(),
        "## Run Coverage",
        "",
        *markdown_table(coverage_rows, overview_columns),
        "",
        "## Per-Run Results",
        "",
        *markdown_table(rows, run_columns),
        "",
        "## Best Run by Model",
        "",
        "默认选择规则改为 survival-first：优先选择 run days 最多的 run，"
        "再用 final networth 和 total sales 做 tie-break。这个规则更符合当前 paper-submit "
        "目标，因为过早破产或结束的 run 不应仅凭短期 networth 被选为 best run。"
        "单 run baseline 只会选择自身。",
        "",
        *markdown_table(best_rows, best_columns),
        "",
        *diagnostic_analysis_lines(rows),
        *stage3_supplier_diagnostic_lines(path.parent),
        "## Files",
        "",
        "- `run_metrics.csv`: per-run flat table。",
        "- `run_metrics.json`: 包含 manifest、metric definitions 和 run 结果的完整 JSON。",
        "- `best_framework_by_model.csv`: 每个模型的默认 selected run。",
        "- `metric_definitions.md`: 独立指标说明文件。",
        "- `selected_llm_action_diagnostics.csv`: selected LLM runs 的 action-level diagnostics。",
        "- `deep_diagnostic_analysis.md`: 模型间差异和 oracle-style heuristic gap 的深度解释。",
        "- `llm_agent_failure_analysis_report.md`: 面向论文立论的 V1/V2 failure-analysis report 和自我审视。",
        "- `four_stage_measurement_framework.md`: 四阶段 operational pipeline 的 metric、diagnostic 和 intervention 框架。",
        "- `supplier_quality_failure_analysis.md`: Stage 3 supplier quality failure 的日志审计报告。",
        "- `supplier_quality_failure_cases.json`: Stage 3 supplier quality failure 的 machine-readable 诊断与案例。",
        "- `supplier_rank_history_first_order_analysis.md`: SQL 历史 supplier rank 与各 run 每个 SKU 第一次下单 supplier rank 的对比诊断。",
        "- `first_order_supplier_rank_by_run.csv` / `first_order_supplier_rank_lines.csv` / `historical_supplier_rank_by_sku.csv`: first-order 与历史 SQL supplier rank 的表格输出。",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze RetailBench paper-submit metrics.")
    parser.add_argument("--manifest", type=Path, default=Path(__file__).with_name("manifest.json"))
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).with_name("outputs"))
    parser.add_argument("--max-days", type=int, default=None)
    parser.add_argument("--input-cost-per-mtok", type=float, default=0.0)
    parser.add_argument("--output-cost-per-mtok", type=float, default=0.0)
    args = parser.parse_args()

    manifest_payload, specs = load_manifest(args.manifest)
    args.output_dir.mkdir(parents=True, exist_ok=True)

    rows = [
        analyze_run(
            spec,
            max_days=args.max_days,
            input_cost_per_mtok=args.input_cost_per_mtok,
            output_cost_per_mtok=args.output_cost_per_mtok,
        )
        for spec in specs
    ]
    best_rows = choose_best_framework(rows)

    write_csv(args.output_dir / "run_metrics.csv", rows)
    write_csv(args.output_dir / "best_framework_by_model.csv", best_rows)
    write_definitions(args.output_dir / "metric_definitions.md")
    write_report(args.output_dir / "report.md", rows, best_rows)

    payload = {
        "manifest": manifest_payload,
        "metric_definitions": METRIC_DEFINITIONS,
        "runs": rows,
        "best_framework_by_model": best_rows,
    }
    (args.output_dir / "run_metrics.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(f"Wrote {len(rows)} run rows to {args.output_dir / 'run_metrics.csv'}")
    print(f"Wrote {len(best_rows)} model selections to {args.output_dir / 'best_framework_by_model.csv'}")
    print(f"Wrote report to {args.output_dir / 'report.md'}")


if __name__ == "__main__":
    main()
