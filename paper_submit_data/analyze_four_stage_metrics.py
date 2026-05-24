#!/usr/bin/env python3
"""Analyze RetailBench runs with the four-stage operational framework."""
import argparse
import csv
import json
import math
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from analyze_metrics import analyze_summary_log, load_manifest  # noqa: E402
from four_stage_action_metrics import analyze_action_conversion  # noqa: E402
from analysis.evaluate_final_metrics import (  # noqa: E402
    ACTION_TOOLS,
    GLOBAL_QUERY_TOOLS,
    QUERY_TOOLS,
    _extract_action_skus,
    _extract_sku_ids,
    _final_db_path,
    _is_executed_business_action,
    _query_category_for_record,
    _score_query_depth_for_action,
    _successful_order_lines,
)
FOLLOWUP_WINDOW_DAYS = 7
HIGH_DEMAND_LOOKBACK_DAYS = 3
HIGH_DEMAND_TOP_K = 10
@dataclass(frozen=True)
class RunSeries:
    acted_by_day: dict[int, set[str]]
    query_by_day: dict[int, set[str]]
    sales_by_day: dict[int, dict[str, float]]
    stockout_by_day: dict[int, set[str]]
    returns_by_day: dict[int, set[str]]
    expired_by_day: dict[int, set[str]]
    action_events: list[tuple[int, str, str]]
    action_counts_by_sku: Counter[str]
    action_day_count: int
    attempted_actions: int
    executed_actions: int
    action_sku_events: int
    query_depth_values: list[float]
    place_order_query_depth_values: list[float]
    modify_price_query_depth_values: list[float]
    evidence_match_count: int
    missing_critical_count: int
    evidence_gap_values: list[int]
    tool_diversity_values: list[int]
    analyzed_days: int
def safe_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None
def fmt(value: Any, digits: int = 4) -> str:
    number = safe_float(value)
    if number is None:
        return "--" if value in (None, "") else str(value)
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    if abs(number) >= 1000:
        return f"{number:,.2f}"
    return f"{number:.{digits}f}"
def avg(values: list[float] | list[int]) -> float | None:
    return float(mean(values)) if values else None
def fraction(num: int, den: int) -> float | None:
    return num / den if den else None
def markdown_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> list[str]:
    lines = [
        "| " + " | ".join(title for title, _ in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(fmt(row.get(key)) for _, key in columns) + " |")
    return lines
TOOL_RE = re.compile(r'"tool"\s*:\s*"([^"]+)"')
STRING_FIELD_RE = re.compile(r'"(?P<key>call_source|source)"\s*:\s*"(?P<value>[^"]+)"')
def fast_tool(line: str) -> str | None:
    match = TOOL_RE.search(line[:400])
    return match.group(1) if match else None
def fast_args(line: str) -> dict[str, Any]:
    marker = '"args"'
    pos = line.find(marker)
    if pos < 0:
        return {}
    start = line.find("{", pos)
    if start < 0:
        return {}
    try:
        value, _ = json.JSONDecoder().raw_decode(line[start:])
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}
def fast_sources(line: str) -> dict[str, str]:
    return {m.group("key"): m.group("value") for m in STRING_FIELD_RE.finditer(line[:800])}
def query_skus(record: dict[str, Any]) -> set[str]:
    args = record.get("args") or {}
    values: set[str] = set()
    raw_skus = args.get("sku_ids")
    if isinstance(raw_skus, list):
        values.update(str(item) for item in raw_skus if item)
    elif raw_skus:
        values.add(str(raw_skus))
    if args.get("sku_id"):
        values.add(str(args["sku_id"]))
    return values
def relevant_query(record: dict[str, Any], sku_id: str) -> bool:
    tool = record.get("tool")
    args = record.get("args") or {}
    if tool in GLOBAL_QUERY_TOOLS:
        return True
    if tool not in QUERY_TOOLS:
        return False
    return bool(args.get("_dynamic_sku_ids") or sku_id in query_skus(record))
def extract_action_skus(record: dict[str, Any]) -> list[str]:
    tool = record.get("tool")
    if tool == "place_order":
        lines = _successful_order_lines(record)
        if lines:
            return [str(line["sku_id"]) for line in lines if line.get("sku_id")]
    return [str(sku) for sku in _extract_action_skus(str(tool), record.get("args") or {}) if sku]
def load_sku_categories(db_path: Path | None) -> dict[str, str]:
    if not db_path or not db_path.exists():
        return {}
    categories: dict[str, tuple[int, str]] = {}
    try:
        conn = sqlite3.connect(str(db_path))
        rows = conn.execute(
            """
            SELECT upc, category, COUNT(*) AS n
            FROM review_records
            WHERE category IS NOT NULL AND category != ''
            GROUP BY upc, category
            """
        ).fetchall()
        conn.close()
    except sqlite3.Error:
        return {}
    for sku, category, count in rows:
        key = str(sku)
        if key not in categories or int(count) > categories[key][0]:
            categories[key] = (int(count), str(category))
    return {sku: category for sku, (_, category) in categories.items()}
def collect_llm_series(run_dir: Path, max_days: int | None) -> RunSeries:
    acted_by_day: dict[int, set[str]] = defaultdict(set)
    query_by_day: dict[int, set[str]] = defaultdict(set)
    sales_by_day: dict[int, dict[str, float]] = {}
    stockout_by_day: dict[int, set[str]] = defaultdict(set)
    returns_by_day: dict[int, set[str]] = defaultdict(set)
    expired_by_day: dict[int, set[str]] = defaultdict(set)
    action_events: list[tuple[int, str, str]] = []
    action_counts_by_sku: Counter[str] = Counter()
    query_depth_values: list[float] = []
    place_order_query_depth_values: list[float] = []
    modify_price_query_depth_values: list[float] = []
    evidence_gap_values: list[int] = []
    tool_diversity_values: list[int] = []
    attempted_actions = executed_actions = action_sku_events = 0
    evidence_match_count = missing_critical_count = 0
    action_days: set[int] = set()
    day_index = 1
    record_index = 0
    day_query_records: list[tuple[int, dict[str, Any]]] = []
    tool_calls_path = run_dir / "tool_calls.jsonl"
    if not tool_calls_path.exists():
        day_index = 0
    else:
        with tool_calls_path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw_line in handle:
                if max_days is not None and day_index > max_days:
                    break
                line = raw_line.strip()
                if not line:
                    continue
                tool = fast_tool(line)
                if not tool:
                    continue
                if tool == "execute_code":
                    continue
                if tool in QUERY_TOOLS:
                    args = fast_args(line)
                    record = {"tool": tool, "args": args}
                    record.update(fast_sources(line))
                    day_query_records.append((record_index, record))
                    for sku in query_skus(record):
                        query_by_day[day_index].add(sku)
                    record_index += 1
                    continue
                if tool in ACTION_TOOLS:
                    attempted_actions += 1
                    try:
                        record = json.loads(line)
                    except json.JSONDecodeError:
                        record_index += 1
                        continue
                    if not _is_executed_business_action(record):
                        record_index += 1
                        continue
                    executed_actions += 1
                    for sku_id in extract_action_skus(record):
                        action_sku_events += 1
                        action_days.add(day_index)
                        acted_by_day[day_index].add(sku_id)
                        action_events.append((day_index, sku_id, str(tool)))
                        action_counts_by_sku[sku_id] += 1
                        relevant = [(idx, prev) for idx, prev in day_query_records if relevant_query(prev, sku_id)]
                        if relevant:
                            evidence_match_count += 1
                            evidence_gap_values.append(record_index - max(idx for idx, _ in relevant))
                        relevant_tools = {str(prev.get("tool")) for _, prev in relevant}
                        tool_diversity_values.append(len(relevant_tools))
                        categories = {
                            category
                            for _, prev in day_query_records
                            for category in [_query_category_for_record(prev, sku_id)]
                            if category
                        }
                        score_item = _score_query_depth_for_action(str(tool), categories)
                        score = score_item.get("score")
                        if score is not None:
                            score_value = float(score)
                            query_depth_values.append(score_value)
                            if tool == "place_order":
                                place_order_query_depth_values.append(score_value)
                            elif tool == "modify_sku_price":
                                modify_price_query_depth_values.append(score_value)
                            if score_value < 1.0:
                                missing_critical_count += 1
                    record_index += 1
                    continue
                if tool != "end_today":
                    continue
                try:
                    end_today = json.loads(line)
                except json.JSONDecodeError:
                    continue
                result_data = {}
                if isinstance(end_today.get("result"), dict):
                    result_data = end_today["result"].get("result") or {}
                if not isinstance(result_data, dict):
                    day_index += 1
                    record_index = 0
                    day_query_records = []
                    continue
                sales = result_data.get("sales_by_sku") or {}
                if isinstance(sales, dict):
                    sales_by_day[day_index] = {
                        str(sku): float(qty)
                        for sku, qty in sales.items()
                        if isinstance(qty, (int, float)) and qty > 0
                    }
                insufficient = result_data.get("insufficient_skus") or []
                if isinstance(insufficient, list):
                    stockout_by_day[day_index] = _extract_sku_ids(insufficient)
                returns = result_data.get("returns_by_sku") or {}
                if isinstance(returns, dict):
                    returns_by_day[day_index] = {
                        str(sku) for sku, qty in returns.items()
                        if isinstance(qty, (int, float)) and qty > 0
                    }
                expired = result_data.get("expired_discount_by_sku") or {}
                if isinstance(expired, dict):
                    expired_by_day[day_index] = {
                        str(sku) for sku, qty in expired.items()
                        if isinstance(qty, (int, float)) and qty > 0
                    }
                day_index += 1
                record_index = 0
                day_query_records = []
    analyzed_days = max(max(sales_by_day.keys(), default=0), day_index - 1)
    return RunSeries(
        acted_by_day=dict(acted_by_day),
        query_by_day=dict(query_by_day),
        sales_by_day=sales_by_day,
        stockout_by_day=dict(stockout_by_day),
        returns_by_day=dict(returns_by_day),
        expired_by_day=dict(expired_by_day),
        action_events=action_events,
        action_counts_by_sku=action_counts_by_sku,
        action_day_count=len(action_days),
        attempted_actions=attempted_actions,
        executed_actions=executed_actions,
        action_sku_events=action_sku_events,
        query_depth_values=query_depth_values,
        place_order_query_depth_values=place_order_query_depth_values,
        modify_price_query_depth_values=modify_price_query_depth_values,
        evidence_match_count=evidence_match_count,
        missing_critical_count=missing_critical_count,
        evidence_gap_values=evidence_gap_values,
        tool_diversity_values=tool_diversity_values,
        analyzed_days=analyzed_days,
    )
def parse_non_llm_start_date(source_path: Path) -> str:
    log_path = source_path / "run_log"
    pattern = re.compile(r"Current date:\s*(\d{4}-\d{2}-\d{2})")
    try:
        for line in log_path.open("r", encoding="utf-8", errors="replace"):
            match = pattern.search(line)
            if match:
                return match.group(1)
    except OSError:
        pass
    return "1991-09-07"
def collect_non_llm_series(source_path: Path, db_path: Path, run_days: int) -> RunSeries:
    start_date = datetime.strptime(parse_non_llm_start_date(source_path), "%Y-%m-%d")
    dates = [(start_date + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(run_days)]
    day_by_date = {date: index + 1 for index, date in enumerate(dates)}
    acted_by_day: dict[int, set[str]] = defaultdict(set)
    sales_by_day: dict[int, dict[str, float]] = defaultdict(dict)
    returns_by_day: dict[int, set[str]] = defaultdict(set)
    expired_by_day: dict[int, set[str]] = defaultdict(set)
    action_events: list[tuple[int, str, str]] = []
    action_counts_by_sku: Counter[str] = Counter()
    conn = sqlite3.connect(str(db_path))
    try:
        for order_date, items_raw in conn.execute("SELECT order_date, items FROM supplier_orders"):
            day = day_by_date.get(str(order_date))
            if not day:
                continue
            try:
                items = json.loads(items_raw)
            except (TypeError, json.JSONDecodeError):
                continue
            for sku, qty in items.items():
                if safe_float(qty) and safe_float(qty) > 0:
                    sku_id = str(sku)
                    acted_by_day[day].add(sku_id)
                    action_events.append((day, sku_id, "place_order"))
                    action_counts_by_sku[sku_id] += 1
        for date, sku, move in conn.execute("SELECT date, upc, move FROM sale_records"):
            day = day_by_date.get(str(date))
            qty = safe_float(move)
            if day and qty and qty > 0:
                sales_by_day[day][str(sku)] = sales_by_day[day].get(str(sku), 0.0) + qty
        for date, sku in conn.execute("SELECT date, sku_id FROM return_records"):
            day = day_by_date.get(str(date))
            if day:
                returns_by_day[day].add(str(sku))
        for loss_date, sku in conn.execute(
            "SELECT loss_date, sku_id FROM product_lifecycle WHERE loss_date IS NOT NULL"
        ):
            day = day_by_date.get(str(loss_date))
            if day:
                expired_by_day[day].add(str(sku))
    finally:
        conn.close()
    return RunSeries(
        acted_by_day=dict(acted_by_day),
        query_by_day={},
        sales_by_day=dict(sales_by_day),
        stockout_by_day={},
        returns_by_day=dict(returns_by_day),
        expired_by_day=dict(expired_by_day),
        action_events=action_events,
        action_counts_by_sku=action_counts_by_sku,
        action_day_count=len(acted_by_day),
        attempted_actions=len(action_events),
        executed_actions=len(action_events),
        action_sku_events=len(action_events),
        query_depth_values=[],
        place_order_query_depth_values=[],
        modify_price_query_depth_values=[],
        evidence_match_count=0,
        missing_critical_count=0,
        evidence_gap_values=[],
        tool_diversity_values=[],
        analyzed_days=run_days,
    )
def high_demand_by_day(series: RunSeries) -> dict[int, set[str]]:
    out: dict[int, set[str]] = {}
    all_days = set(series.sales_by_day) | set(series.stockout_by_day)
    for day in all_days:
        ranked = sorted(
            series.sales_by_day.get(day, {}).items(),
            key=lambda item: item[1],
            reverse=True,
        )
        high = {sku for sku, _ in ranked[:HIGH_DEMAND_TOP_K]}
        high.update(series.stockout_by_day.get(day, set()))
        out[day] = high
    return out
def any_sales(series: RunSeries, sku_id: str, start_day: int, window: int) -> bool:
    for day in range(start_day, start_day + window + 1):
        if series.sales_by_day.get(day, {}).get(sku_id, 0) > 0:
            return True
    return False
def has_attention(series: RunSeries, sku_id: str, start_day: int, end_day: int) -> bool:
    for day in range(start_day, end_day + 1):
        if sku_id in series.acted_by_day.get(day, set()) or sku_id in series.query_by_day.get(day, set()):
            return True
    return False
def jaccard(a: set[str], b: set[str]) -> float | None:
    if not a and not b:
        return None
    return len(a & b) / len(a | b) if (a or b) else None
def stage_metrics(series: RunSeries, categories: dict[str, str]) -> dict[str, Any]:
    action_to_sales_hits = sum(
        1 for day, sku, _ in series.action_events
        if any_sales(series, sku, day, FOLLOWUP_WINDOW_DAYS)
    )
    high_by_day = high_demand_by_day(series)
    high_total = 0
    high_missed = 0
    for day, skus in high_by_day.items():
        for sku in skus:
            high_total += 1
            if not any(
                sku in series.acted_by_day.get(d, set())
                for d in range(max(1, day - HIGH_DEMAND_LOOKBACK_DAYS), day + 1)
            ):
                high_missed += 1
    total_actions = sum(series.action_counts_by_sku.values())
    hhi = (
        sum((count / total_actions) ** 2 for count in series.action_counts_by_sku.values())
        if total_actions else None
    )
    top10_share = (
        sum(count for _, count in series.action_counts_by_sku.most_common(10)) / total_actions
        if total_actions else None
    )
    category_count = len({
        categories[sku] for sku in series.action_counts_by_sku
        if sku in categories
    })
    query_sku_days = sum(len(v) for v in series.query_by_day.values())
    query_action_overlap = 0
    for day, skus in series.query_by_day.items():
        query_action_overlap += len(skus & series.acted_by_day.get(day, set()))
    follow_query_or_action = 0
    follow_action = 0
    for day, sku, _ in series.action_events:
        if has_attention(series, sku, day + 1, day + FOLLOWUP_WINDOW_DAYS):
            follow_query_or_action += 1
        if any(sku in series.acted_by_day.get(d, set()) for d in range(day + 1, day + FOLLOWUP_WINDOW_DAYS + 1)):
            follow_action += 1
    event_items: list[tuple[int, str]] = []
    for event_map in (series.stockout_by_day, series.returns_by_day, series.expired_by_day):
        for day, skus in event_map.items():
            event_items.extend((day, sku) for sku in skus)
    unresolved = 0
    latencies: list[int] = []
    for day, sku in event_items:
        found = None
        for offset in range(1, FOLLOWUP_WINDOW_DAYS + 1):
            if has_attention(series, sku, day + offset, day + offset):
                found = offset
                break
        if found is None:
            unresolved += 1
        else:
            latencies.append(found)
    repeated_total = 0
    repeated_without_attention = 0
    days_by_event_sku: dict[str, list[int]] = defaultdict(list)
    for day, sku in event_items:
        days_by_event_sku[sku].append(day)
    for sku, days in days_by_event_sku.items():
        ordered = sorted(set(days))
        for prev, curr in zip(ordered, ordered[1:]):
            repeated_total += 1
            if not has_attention(series, sku, prev + 1, curr):
                repeated_without_attention += 1
    continuity_values = []
    for day in range(2, series.analyzed_days + 1):
        value = jaccard(series.acted_by_day.get(day - 1, set()), series.acted_by_day.get(day, set()))
        if value is not None:
            continuity_values.append(value)
    return {
        "s1_avg_acted_skus_per_day": (
            sum(len(v) for v in series.acted_by_day.values()) / series.analyzed_days
            if series.analyzed_days else None
        ),
        "s1_total_distinct_acted_skus": len(series.action_counts_by_sku),
        "s1_acted_sku_events": series.action_sku_events,
        "s1_action_to_sales_overlap_7d": fraction(action_to_sales_hits, len(series.action_events)),
        "s1_missed_high_demand_rate": fraction(high_missed, high_total),
        "s1_action_concentration_hhi": hhi,
        "s1_top10_action_share": top10_share,
        "s1_category_coverage": category_count or None,
        "s2_query_depth": avg(series.query_depth_values),
        "s2_place_order_query_depth": avg(series.place_order_query_depth_values),
        "s2_modify_price_query_depth": avg(series.modify_price_query_depth_values),
        "s2_evidence_action_match_rate": fraction(series.evidence_match_count, series.action_sku_events),
        "s2_missing_critical_evidence_rate": fraction(series.missing_critical_count, series.action_sku_events),
        "s2_avg_evidence_call_gap": avg(series.evidence_gap_values),
        "s2_avg_pre_action_tool_diversity": avg(series.tool_diversity_values),
        "s2_query_to_action_overlap_same_day": fraction(query_action_overlap, query_sku_days),
        "s3_attempted_business_actions": series.attempted_actions,
        "s3_executed_business_actions": series.executed_actions,
        "s3_invalid_or_blocked_action_rate": 1 - fraction(series.executed_actions, series.attempted_actions)
        if series.attempted_actions else None,
        "s4_followup_query_or_action_rate_7d": fraction(follow_query_or_action, len(series.action_events)),
        "s4_followup_action_rate_7d": fraction(follow_action, len(series.action_events)),
        "s4_unresolved_event_rate_7d": fraction(unresolved, len(event_items)),
        "s4_avg_response_latency_days": avg(latencies),
        "s4_focus_continuity_jaccard": avg(continuity_values),
        "s4_repeated_error_without_intervention_rate": fraction(repeated_without_attention, repeated_total),
    }
def load_run_metrics(output_dir: Path) -> dict[str, dict[str, Any]]:
    path = output_dir / "run_metrics.csv"
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        return {row["run_id"]: row for row in csv.DictReader(handle)}
def analyze_spec(
    spec: Any,
    base_rows: dict[str, dict[str, Any]],
    max_days: int | None,
) -> dict[str, Any]:
    base = dict(base_rows.get(spec.run_id, {}))
    run_days = int(safe_float(base.get("run_days")) or 0)
    db_path = spec.database_path
    has_tool_trace = spec.source_format != "summary_log" and (spec.source_path / "tool_calls.jsonl").exists()
    if spec.run_type == "llm" or has_tool_trace:
        db_path = _final_db_path(spec.source_path)
        series = collect_llm_series(spec.source_path, max_days=max_days)
        action = analyze_action_conversion(spec.source_path, max_days=max_days)
    else:
        if not run_days:
            summary = analyze_summary_log(spec.source_path)
            run_days = int(safe_float(summary.get("run_days")) or 0)
        if spec.database_path is None:
            raise ValueError(f"database_path is required for summary-only non-LLM run: {spec.run_id}")
        series = collect_non_llm_series(spec.source_path, spec.database_path, run_days)
        action = {}
    categories = load_sku_categories(db_path)
    row: dict[str, Any] = {
        "model": spec.model,
        "framework": spec.framework,
        "run_id": spec.run_id,
        "run_type": spec.run_type,
        "run_days": safe_float(base.get("run_days")) or run_days,
        "final_networth": safe_float(base.get("final_networth")),
        "total_sales": safe_float(base.get("total_sales")),
        "avg_daily_sold_skus": safe_float(base.get("avg_daily_sold_skus")),
        "return_ratio": safe_float(base.get("return_ratio")),
        "expired_ratio": safe_float(base.get("expired_ratio")),
        "stockout_ratio": safe_float(base.get("stockout_ratio")),
    }
    row.update(stage_metrics(series, categories))
    row.update({key: value for key, value in action.items() if key.startswith("s3_")})
    return row
def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
def selected_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["model"])].append(row)
    return [
        sorted(
            items,
            key=lambda row: (
                safe_float(row.get("run_days")) or -math.inf,
                safe_float(row.get("final_networth")) or -math.inf,
                safe_float(row.get("total_sales")) or -math.inf,
            ),
            reverse=True,
        )[0]
        for _, items in sorted(grouped.items())
    ]
def corr(rows: list[dict[str, Any]], x_key: str, y_key: str) -> float | None:
    pairs = [
        (safe_float(row.get(x_key)), safe_float(row.get(y_key)))
        for row in rows
    ]
    valid = [(x, y) for x, y in pairs if x is not None and y is not None]
    if len(valid) < 2:
        return None
    mx = mean(x for x, _ in valid)
    my = mean(y for _, y in valid)
    num = sum((x - mx) * (y - my) for x, y in valid)
    den_x = sum((x - mx) ** 2 for x, _ in valid)
    den_y = sum((y - my) ** 2 for _, y in valid)
    return num / math.sqrt(den_x * den_y) if den_x and den_y else None
def metric_range(rows: list[dict[str, Any]], key: str) -> str:
    values = [safe_float(row.get(key)) for row in rows]
    clean = [v for v in values if v is not None]
    if not clean:
        return "--"
    return f"{fmt(min(clean))} - {fmt(max(clean))}, mean {fmt(mean(clean))}"
def render_report(rows: list[dict[str, Any]]) -> str:
    selected = selected_rows(rows)
    llm_selected = [row for row in selected if row.get("run_type") == "llm"]
    oracle = next((row for row in selected if row.get("run_type") != "llm"), None)
    all_columns = [
        ("Model", "model"), ("Framework", "framework"), ("Days", "run_days"),
        ("Networth", "final_networth"), ("Sales", "total_sales"),
        ("Acted/day", "s1_avg_acted_skus_per_day"),
        ("MissHD", "s1_missed_high_demand_rate"),
        ("QDepth", "s2_query_depth"), ("ActCorr", "s3_action_correction"),
        ("Follow7d", "s4_followup_query_or_action_rate_7d"),
        ("Unresolved", "s4_unresolved_event_rate_7d"),
    ]
    s1_cols = [
        ("Model", "model"), ("Framework", "framework"), ("Days", "run_days"),
        ("Acted/day", "s1_avg_acted_skus_per_day"), ("Distinct acted", "s1_total_distinct_acted_skus"),
        ("Action->Sales7d", "s1_action_to_sales_overlap_7d"),
        ("Missed high-demand", "s1_missed_high_demand_rate"),
        ("Top10 share", "s1_top10_action_share"), ("Categories", "s1_category_coverage"),
    ]
    s2_cols = [
        ("Model", "model"), ("Framework", "framework"), ("QDepth", "s2_query_depth"),
        ("Order QDepth", "s2_place_order_query_depth"), ("Price QDepth", "s2_modify_price_query_depth"),
        ("Evidence match", "s2_evidence_action_match_rate"),
        ("Missing critical", "s2_missing_critical_evidence_rate"),
        ("Tool diversity", "s2_avg_pre_action_tool_diversity"),
        ("Call gap", "s2_avg_evidence_call_gap"),
    ]
    s3_cols = [
        ("Model", "model"), ("Framework", "framework"), ("ActionCorr", "s3_action_correction"),
        ("OrderCorr", "s3_place_order_correction"), ("PriceCorr", "s3_modify_price_correction"),
        ("QualityTop1", "s3_raw_quality_top1_hit"), ("QRatio", "s3_quality_ratio_mean"),
        ("Qty/DailySales", "s3_order_qty_to_avg_daily_sales_mean"),
        ("PriceDist%", "s3_modify_price_distance_pct_mean"),
        ("Blocked", "s3_invalid_or_blocked_action_rate"),
    ]
    s4_cols = [
        ("Model", "model"), ("Framework", "framework"), ("Follow q/a", "s4_followup_query_or_action_rate_7d"),
        ("Follow action", "s4_followup_action_rate_7d"), ("Unresolved", "s4_unresolved_event_rate_7d"),
        ("Latency", "s4_avg_response_latency_days"), ("Continuity", "s4_focus_continuity_jaccard"),
        ("Repeat no-attn", "s4_repeated_error_without_intervention_rate"),
    ]
    corr_rows = [
        {"metric": key, "corr_with_days": corr(llm_selected, key, "run_days"), "corr_with_networth": corr(llm_selected, key, "final_networth")}
        for key in [
            "s1_avg_acted_skus_per_day",
            "s1_missed_high_demand_rate",
            "s2_query_depth",
            "s3_action_correction",
            "s3_raw_quality_top1_hit",
            "s4_followup_query_or_action_rate_7d",
            "s4_unresolved_event_rate_7d",
        ]
    ]
    lines = [
        "# RetailBench Four-Stage Run Analysis",
        "",
        "## Scope, Metric Definition, and Analysis Logic",
        "",
        (f"本报告覆盖 `manifest.json` 中的 {len(rows)} 个 runs：LLM runs 与 Non-LLM Heuristic 放在同一张表中。所有 best-run 解释仍采用 survival-first 选择规则；stage-level 表格则保留每一个 run。"),
        "",
        "窗口定义：`Action->Sales7d` 表示 action SKU 在当天到未来 7 天是否产生销售；`Missed high-demand` 使用每日销量 top-10 SKU 加 stockout SKU 作为事后 high-demand signal，检查过去 3 天至当天是否被 action 覆盖；`Follow q/a` 检查 action 后 7 天内是否再次 query 或 action 同一 SKU。这些是诊断指标，不是声称 agent 当时可以看到未来销量。",
        "",
        "Non-LLM Heuristic 若提供 `tool_calls.jsonl`，会和 LLM runs 一样计算 query-depth、"
        "action-correction 和 follow-up trace 指标；legacy summary-only non-LLM 输入才将 tool-trace 指标记为 N/A。"
        "\n\n## How the Metrics Were Analyzed\n\n"
        "分析单位是 run。基础经营指标来自 `run_metrics.csv`；行为指标来自每个 run 的 `tool_calls.jsonl` 与 final records DB；"
        "legacy summary-only Non-LLM 指标回退到 `run_log` 与 `records_db`。"
        "Stage 1 先按天抽取实际执行的 `place_order`/`modify_sku_price` SKU，再计算 acted SKU/day、distinct acted SKU、category coverage、action concentration；"
        "`Action->Sales7d` 检查 action SKU 当天到未来 7 天是否销售，`Missed high-demand` 把每日 sales top-10 与 stockout SKU 当作事后 high-demand signal，并检查过去 3 天到当天是否被 action 覆盖。"
        "Stage 2 对每个已执行 action 只看同一天 action 之前的 query，把 query 映射到 inventory、sales history、supplier price、supplier return/rating、current price、cost 等 evidence categories；"
        "`QDepth` 是命中的 required categories 比例，`Evidence match` 要求 query 覆盖同 SKU 或全局上下文，`Missing critical` 表示至少一个 required category 缺失。"
        "Stage 3 对每个 LLM business action 重新做 reference comparison：订货 action 比较 selected supplier 是否等于当前可见 proxy-quality 最优 supplier，价格 action 比较 new price 是否落在历史销量估计最优价的 ±10% 内；"
        "同时报告 raw supplier quality top-1 hit、quality ratio、order quantity / recent daily sales、price distance percent 和 blocked action rate。"
        "Stage 4 把 action SKU、stockout、return、expiration 作为需要后续处理的事件，检查 7 天内是否再次 query/action、首次响应延迟、相邻 action SKU set 的 Jaccard continuity，以及重复事件之间是否完全没有 attention。"
        "最后，报告保留 all-run stage tables；best-run 解释采用 survival-first selection；correlation 只在 7 个 survival-first selected LLM runs 上做 descriptive diagnostics，不作为显著性或因果检验。",
        "",
        "## Executive Summary",
        "",
        (
            f"LLM selected runs 的 survival range 为 {metric_range(llm_selected, 'run_days')}；"
            f"acted SKU/day range 为 {metric_range(llm_selected, 's1_avg_acted_skus_per_day')}；"
            f"action correction range 为 {metric_range(llm_selected, 's3_action_correction')}。"
        ),
        "",
        (
            "主要问题不是单点工具调用失败，而是四阶段 pipeline 的复合失败：弱 run 覆盖 SKU 少、"
            "漏掉高需求 SKU；中强 run 即便 query depth 高，也经常不能把 evidence 转成正确 supplier/quantity/price；"
            "长期 follow-up 不稳定，使 stockout、returns、expiration 等 delayed signals 无法及时闭环。"
        ),
        "",
    ]
    if oracle:
        best_llm = max(llm_selected, key=lambda row: safe_float(row.get("final_networth")) or -math.inf)
        lines.extend([
            (
                f"Non-LLM Heuristic 达到 {fmt(oracle['run_days'])} 天、networth {fmt(oracle['final_networth'])}、"
                f"sales {fmt(oracle['total_sales'])}。best LLM by networth 是 {best_llm['model']} / "
                f"{best_llm['framework']}，networth gap 仍为 "
                f"{fmt((safe_float(oracle['final_networth']) or 0) - (safe_float(best_llm['final_networth']) or 0))}。"
            ),
            "",
        ])
    lines.extend([
        "## All Runs Compact View",
        "",
        *markdown_table(rows, all_columns),
        "",
        "## Stage 1: SKU Candidate Selection",
        "",
        "这一阶段衡量模型有没有把注意力放到足够多、足够相关的 SKU 上。高 `Missed high-demand` 表示事后看重要 SKU 没被及时纳入 action；高 `Top10 share` 表示 action 集中在少数 SKU 上。",
        "",
        *markdown_table(rows, s1_cols),
        "",
        "问题总结：弱模型通常不是只做错单个 SKU，而是 SKU 管理覆盖不足。`Missed high-demand` 和 `Acted/day` 应该和 survival 一起读；低 stockout 但低 acted/day 可能只是卖得少、覆盖窄，不代表策略健康。",
        "",
        "## Stage 2: Evidence Acquisition",
        "",
        "这一阶段衡量 action 前证据是否足够、是否和 action SKU 匹配。`QDepth` 来自 required evidence categories；`Evidence match` 是至少查过同 SKU或全局上下文的 action 比例。",
        "",
        *markdown_table(rows, s2_cols),
        "",
        "问题总结：高 `QDepth` 不是充分条件。多个强 run 的 query depth 很高，但 Stage 3 的 action correction 仍然低，说明 failure point 往往在 evidence interpretation 和 action translation，而不是单纯少查工具。",
        "",
        "## Stage 3: Action Conversion",
        "",
        "这一阶段衡量模型能否把证据转成正确 supplier、quantity 和 price。`QualityTop1` 反映 supplier quality selection；`PriceDist%` 反映调价离估计最优价格的距离；`Qty/DailySales` 反映订货量相对近期需求的尺度。",
        "",
        *markdown_table(rows, s3_cols),
        "",
        "问题总结：这是当前最有数据支撑的核心瓶颈。LLM 经常查到信息，但 supplier quality hit、action correction 和 price distance 表现不稳；这解释了为什么 survival 相同的 run 也会和 heuristic 有很大 networth gap。",
        "",
        "## Stage 4: Temporal Follow-Up",
        "",
        "这一阶段衡量模型是否持续跟踪自己的动作和 delayed outcome。`Unresolved` 表示 stockout/return/expiration SKU 在 7 天内没有再次被 query 或 action；`Continuity` 是相邻 action SKU set 的 Jaccard。",
        "",
        *markdown_table(rows, s4_cols),
        "",
        "问题总结：follow-up 是长 horizon benchmark 的放大器。一次订货或调价未必立刻失败，但如果 returns、stockout、expiration 后没有 revisit，错误会持续积累为现金流和库存周转问题。",
        "",
        "## Correlation Diagnostics on Survival-First Selected LLM Runs",
        "",
        *markdown_table(corr_rows, [("Metric", "metric"), ("Corr Days", "corr_with_days"), ("Corr Networth", "corr_with_networth")]),
        "",
        "这些 correlation 只用于描述当前 selected runs，不构成显著性检验。样本数只有 7 个 LLM selected runs，适合支持 mechanism hypothesis，不适合做强因果 claim。",
        "",
        "## Intervention Mapping",
        "",
        "| Stage | Main observed problem | Recommended intervention | Expected measurable movement |",
        "| --- | --- | --- | --- |",
        "| Stage 1 | SKU 覆盖不足、漏掉 high-demand SKU、action 过度集中 | SKU candidate generator + daily watchlist | `Acted/day` 上升，`Missed high-demand` 下降，category coverage 上升 |",
        "| Stage 2 | 查证据和 action SKU 不完全匹配，critical evidence 缺失 | Evidence-gated action template | `QDepth` 和 `Evidence match` 上升，`Missing critical` 下降 |",
        "| Stage 3 | supplier/quantity/price 转换不稳定 | action validator + supplier-quality memory + constrained decoding | `ActionCorr`、`QualityTop1` 上升，`PriceDist%` 和 return ratio 下降 |",
        "| Stage 4 | delayed feedback 没有闭环 | persistent SKU watchlist + scheduler | `Follow q/a` 上升，`Unresolved` 和 repeated-error rate 下降 |",
    ])
    return "\n".join(lines)
def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze RetailBench four-stage behavior metrics.")
    parser.add_argument("--manifest", type=Path, default=SCRIPT_DIR / "manifest.json")
    parser.add_argument("--output-dir", type=Path, default=SCRIPT_DIR / "outputs")
    parser.add_argument("--max-days", type=int, default=None)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest, specs = load_manifest(args.manifest)
    base_rows = load_run_metrics(args.output_dir)
    rows = [analyze_spec(spec, base_rows, max_days=args.max_days) for spec in specs]
    csv_path = args.output_dir / "four_stage_metrics.csv"
    json_path = args.output_dir / "four_stage_metrics.json"
    report_path = args.output_dir / "four_stage_analysis_report.md"
    write_csv(csv_path, rows)
    json_path.write_text(
        json.dumps(
            {
                "manifest": manifest,
                "window_days": FOLLOWUP_WINDOW_DAYS,
                "high_demand_top_k": HIGH_DEMAND_TOP_K,
                "rows": rows,
                "selected_rows": selected_rows(rows),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    report_path.write_text(render_report(rows), encoding="utf-8")
    print(f"Wrote {csv_path}")
    print(f"Wrote {json_path}")
if __name__ == "__main__":
    main()
