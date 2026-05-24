#!/usr/bin/env python3
"""Streaming action-conversion diagnostics for four-stage analysis."""
import json
import math
import re
import sqlite3
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from analysis.evaluate_final_metrics import (  # noqa: E402
    ACTION_TOOLS,
    _avg,
    _best_proxy_quality_supplier_quote,
    _final_db_path,
    _is_executed_business_action,
    _optimal_price_from_history,
    _quote_quality_score,
    _query_avg_daily_sales,
    _successful_order_lines,
    _supplier_quotes_on_date,
    _supplier_manager_for_run,
)


TOOL_RE = re.compile(r'"tool"\s*:\s*"([^"]+)"')


def fast_tool(line: str) -> str | None:
    match = TOOL_RE.search(line[:400])
    return match.group(1) if match else None


def iter_action_records(tool_calls_path: Path, max_days: int | None = None):
    if not tool_calls_path.exists():
        return
    day = 1
    with tool_calls_path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw_line in handle:
            if max_days is not None and day > max_days:
                break
            line = raw_line.strip()
            if not line:
                continue
            tool = fast_tool(line)
            if tool == "end_today":
                day += 1
                continue
            if tool not in ACTION_TOOLS:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            yield record


def finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    pos = (len(ordered) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return ordered[int(pos)]
    return ordered[lo] * (hi - pos) + ordered[hi] * (pos - lo)


def supplier_rank_info(conn, supplier_manager, sku_id: str, current_date: str) -> dict[str, Any]:
    quotes = _supplier_quotes_on_date(conn, sku_id, current_date)
    price_sorted = sorted(quotes, key=lambda q: (q["price"], q["supplier_id"]))
    price_rank = {q["supplier_id"]: i + 1 for i, q in enumerate(price_sorted)}
    price_by_supplier = {q["supplier_id"]: float(q["price"]) for q in quotes}
    quality_by_supplier = {
        q["supplier_id"]: _quote_quality_score(supplier_manager, q["supplier_id"], sku_id, current_date)
        for q in quotes
    }
    quality_quotes = [
        {**q, "quality_score": float(quality_by_supplier[q["supplier_id"]])}
        for q in quotes if quality_by_supplier.get(q["supplier_id"]) is not None
    ]
    quality_sorted = sorted(
        quality_quotes,
        key=lambda q: (-q["quality_score"], q["price"], q["supplier_id"]),
    )
    quality_rank = {q["supplier_id"]: i + 1 for i, q in enumerate(quality_sorted)}
    return {
        "candidate_count": len(quotes),
        "price_rank": price_rank,
        "price_by_supplier": price_by_supplier,
        "cheapest": price_sorted[0] if price_sorted else None,
        "quality_rank": quality_rank,
        "quality_by_supplier": quality_by_supplier,
        "best_quality": quality_sorted[0] if quality_sorted else None,
    }


def analyze_action_conversion(run_dir: Path, max_days: int | None = None) -> dict[str, Any]:
    db_path = _final_db_path(run_dir)
    if not db_path or not db_path.exists():
        return {"score": None, "error": "missing_records_db"}

    order_total = order_correct = 0
    price_total = price_correct = 0
    skipped = defaultdict(int)
    quality_sources = defaultdict(int)
    raw_quality_count = raw_quality_top1 = 0
    raw_quality_regrets: list[float] = []
    raw_quality_ratios: list[float] = []
    price_first_count = price_first_top1 = 0
    price_regrets: list[float] = []
    price_premiums: list[float] = []
    order_qty_to_avg_daily_sales: list[float] = []
    modify_prices: list[float] = []
    modify_price_distance_pct: list[float] = []
    modify_price_signed_distance_pct: list[float] = []
    supplier_price_ranks: list[float] = []
    supplier_quality_ranks: list[float] = []
    supplier_candidate_counts: list[float] = []
    rank_cache: dict[tuple[str, str], dict[str, Any]] = {}
    best_proxy_cache: dict[tuple[str, str], dict[str, Any] | None] = {}
    avg_sales_cache: dict[tuple[str, str], float] = {}
    optimal_price_cache: dict[tuple[str, str, float], float | None] = {}

    conn = sqlite3.connect(str(db_path))
    supplier_manager = _supplier_manager_for_run(run_dir)
    try:
        for record in iter_action_records(run_dir / "tool_calls.jsonl", max_days=max_days):
            tool = record.get("tool", "")
            if not _is_executed_business_action(record):
                continue
            args = record.get("args", {})
            current_date = str(record.get("current_date") or "")

            if tool == "place_order":
                supplier_id = str(args.get("supplier_id") or "")
                for line in _successful_order_lines(record):
                    sku_id = str(line.get("sku_id") or "")
                    if not sku_id or not supplier_id:
                        skipped["order_missing_args"] += 1
                        continue

                    rank_key = (sku_id, current_date)
                    info = rank_cache.setdefault(rank_key, supplier_rank_info(conn, supplier_manager, sku_id, current_date))
                    price_rank = info["price_rank"].get(supplier_id)
                    if price_rank is not None:
                        supplier_candidate_counts.append(float(info["candidate_count"]))
                        supplier_price_ranks.append(float(price_rank))
                        price_first_count += 1
                        price_first_top1 += int(price_rank == 1)
                        cheapest = info.get("cheapest")
                        selected_price = info["price_by_supplier"].get(supplier_id)
                        if cheapest and selected_price is not None:
                            cheapest_price = float(cheapest["price"])
                            regret = max(selected_price - cheapest_price, 0.0)
                            price_regrets.append(regret)
                            if cheapest_price > 0:
                                price_premiums.append(regret / cheapest_price)
                    quality_rank = info["quality_rank"].get(supplier_id)
                    if quality_rank is not None:
                        supplier_quality_ranks.append(float(quality_rank))
                        raw_quality_count += 1
                        raw_quality_top1 += int(quality_rank == 1)
                        best_quality = info.get("best_quality")
                        selected_quality = info["quality_by_supplier"].get(supplier_id)
                        if best_quality and selected_quality is not None:
                            best_value = float(best_quality["quality_score"])
                            selected_value = float(selected_quality)
                            raw_quality_regrets.append(max(best_value - selected_value, 0.0))
                            if best_value > 0:
                                raw_quality_ratios.append(selected_value / best_value)

                    quantity = finite(line.get("quantity"))
                    avg_key = (sku_id, current_date)
                    if avg_key not in avg_sales_cache:
                        avg_sales_cache[avg_key] = _query_avg_daily_sales(conn, sku_id, current_date, days=30)
                    avg_daily_sales = avg_sales_cache[avg_key]
                    if quantity is not None and avg_daily_sales > 0:
                        order_qty_to_avg_daily_sales.append(quantity / avg_daily_sales)
                    elif quantity is not None:
                        skipped["order_missing_avg_daily_sales"] += 1

                    best_key = (sku_id, current_date)
                    if best_key not in best_proxy_cache:
                        best_proxy_cache[best_key] = _best_proxy_quality_supplier_quote(conn, sku_id, current_date)
                    best = best_proxy_cache[best_key]
                    if not best:
                        skipped["order_missing_quality_reference"] += 1
                        continue
                    quality_sources[str(best.get("quality_source") or "unknown")] += 1
                    order_total += 1
                    if supplier_id == best["supplier_id"]:
                        order_correct += 1

            elif tool == "modify_sku_price":
                sku_id = str(args.get("sku_id") or "")
                new_price = finite(args.get("new_price"))
                if new_price is None:
                    skipped["price_invalid_new_price"] += 1
                    continue
                best_key = (sku_id, current_date)
                if best_key not in best_proxy_cache:
                    best_proxy_cache[best_key] = _best_proxy_quality_supplier_quote(conn, sku_id, current_date)
                best = best_proxy_cache[best_key]
                if not best:
                    skipped["price_missing_quality_reference"] += 1
                    continue
                quality_sources[str(best.get("quality_source") or "unknown")] += 1
                optimal_key = (sku_id, current_date, float(best["price"]))
                if optimal_key not in optimal_price_cache:
                    optimal_price_cache[optimal_key] = _optimal_price_from_history(conn, sku_id, current_date, float(best["price"]))
                optimal_price = optimal_price_cache[optimal_key]
                if optimal_price is None:
                    skipped["price_missing_history_reference"] += 1
                    continue
                price_total += 1
                modify_prices.append(new_price)
                if optimal_price > 0 and new_price <= 50:
                    signed = (new_price - optimal_price) / optimal_price * 100.0
                    modify_price_signed_distance_pct.append(signed)
                    modify_price_distance_pct.append(abs(signed))
                elif new_price > 50:
                    skipped["price_distance_outlier_new_price_gt_50"] += 1
                if 0.9 * optimal_price <= new_price <= 1.1 * optimal_price:
                    price_correct += 1
    finally:
        conn.close()

    total = order_total + price_total
    correct = order_correct + price_correct
    result = {
        "score": correct / total if total else None,
        "correct": correct,
        "count": total,
        "place_order_score": order_correct / order_total if order_total else None,
        "place_order_correct": order_correct,
        "place_order_count": order_total,
        "modify_sku_price_score": price_correct / price_total if price_total else None,
        "modify_sku_price_correct": price_correct,
        "modify_sku_price_count": price_total,
        "raw_quality_top1_hit": raw_quality_top1 / raw_quality_count if raw_quality_count else None,
        "raw_quality_count": raw_quality_count,
        "raw_quality_top1_correct": raw_quality_top1,
        "quality_regret_mean": _avg(raw_quality_regrets),
        "quality_ratio_mean": _avg(raw_quality_ratios),
        "price_first_top1_hit": price_first_top1 / price_first_count if price_first_count else None,
        "price_first_count": price_first_count,
        "price_first_correct": price_first_top1,
        "price_regret_mean": _avg(price_regrets),
        "price_premium_mean": _avg(price_premiums),
        "order_qty_to_avg_daily_sales_mean": _avg(order_qty_to_avg_daily_sales),
        "order_qty_to_avg_daily_sales_count": len(order_qty_to_avg_daily_sales),
        "modify_price_distance_pct_mean": _avg(modify_price_distance_pct),
        "modify_price_distance_pct_median": percentile(modify_price_distance_pct, 0.5),
        "modify_price_distance_pct_p90": percentile(modify_price_distance_pct, 0.9),
        "modify_price_distance_pct_max": max(modify_price_distance_pct) if modify_price_distance_pct else None,
        "modify_price_distance_pct_count": len(modify_price_distance_pct),
        "modify_price_signed_distance_pct_mean": _avg(modify_price_signed_distance_pct),
        "modify_price_above_optimal_rate": (
            sum(1 for value in modify_price_signed_distance_pct if value > 0) / len(modify_price_signed_distance_pct)
            if modify_price_signed_distance_pct else None
        ),
        "modify_price_below_optimal_rate": (
            sum(1 for value in modify_price_signed_distance_pct if value < 0) / len(modify_price_signed_distance_pct)
            if modify_price_signed_distance_pct else None
        ),
        "modify_price_min": min(modify_prices) if modify_prices else None,
        "modify_price_max": max(modify_prices) if modify_prices else None,
        "supplier_price_rank_mean": _avg(supplier_price_ranks),
        "supplier_quality_rank_mean": _avg(supplier_quality_ranks),
        "supplier_candidate_count_mean": _avg(supplier_candidate_counts),
        "supplier_price_first_rate": price_first_top1 / price_first_count if price_first_count else None,
        "supplier_quality_first_rate": raw_quality_top1 / raw_quality_count if raw_quality_count else None,
        "quality_reference_sources": dict(quality_sources),
        "skipped": dict(skipped),
    }
    result.update({
        "s3_action_correction": result["score"],
        "s3_action_eval_count": result["count"],
        "s3_place_order_correction": result["place_order_score"],
        "s3_place_order_eval_count": result["place_order_count"],
        "s3_modify_price_correction": result["modify_sku_price_score"],
        "s3_modify_price_eval_count": result["modify_sku_price_count"],
        "s3_raw_quality_top1_hit": result["raw_quality_top1_hit"],
        "s3_quality_regret_mean": result["quality_regret_mean"],
        "s3_quality_ratio_mean": result["quality_ratio_mean"],
        "s3_order_qty_to_avg_daily_sales_mean": result["order_qty_to_avg_daily_sales_mean"],
        "s3_modify_price_distance_pct_mean": result["modify_price_distance_pct_mean"],
        "s3_modify_price_distance_pct_median": result["modify_price_distance_pct_median"],
        "s3_modify_price_distance_pct_p90": result["modify_price_distance_pct_p90"],
        "s3_modify_price_distance_pct_max": result["modify_price_distance_pct_max"],
        "s3_modify_price_signed_distance_pct_mean": result["modify_price_signed_distance_pct_mean"],
        "s3_modify_price_above_optimal_rate": result["modify_price_above_optimal_rate"],
        "s3_modify_price_below_optimal_rate": result["modify_price_below_optimal_rate"],
        "s3_supplier_price_rank_mean": result["supplier_price_rank_mean"],
        "s3_supplier_quality_rank_mean": result["supplier_quality_rank_mean"],
        "s3_supplier_candidate_count_mean": result["supplier_candidate_count_mean"],
        "s3_supplier_price_first_rate": result["supplier_price_first_rate"],
        "s3_supplier_quality_first_rate": result["supplier_quality_first_rate"],
    })
    return result
