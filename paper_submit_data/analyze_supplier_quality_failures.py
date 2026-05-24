#!/usr/bin/env python3
"""Analyze why LLM runs miss the highest-quality supplier."""
from __future__ import annotations

import csv
import json
import math
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from analyze_metrics import RunSpec, load_manifest  # noqa: E402
from analysis.evaluate_final_metrics import (  # noqa: E402
    _is_executed_business_action,
    _load_run_config,
    _resolve_repo_path,
    _successful_order_lines,
)
from supplier_quality_report_utils import render_report  # noqa: E402


OUTPUT_DIR = SCRIPT_DIR / "outputs"
METRICS_CSV = OUTPUT_DIR / "four_stage_metrics.csv"
DETAILS_JSON = OUTPUT_DIR / "supplier_quality_failure_cases.json"
REPORT_MD = OUTPUT_DIR / "supplier_quality_failure_analysis.md"
MAX_SAMPLE_LINES_PER_RUN = 30

QUALITY_PROXY_TOOLS = {
    "view_supplier_returns_avg_rate",
    "view_sku_avg_ratings",
    "view_sku_reviews",
}

PRICE_TOOLS = {
    "view_current_date_supplier_prices",
    "view_supplier_price_history",
}


@dataclass(frozen=True, order=True)
class BestRunKey:
    run_days: float
    final_networth: float
    total_sales: float


def finite_float(value: Any, default: float = float("-inf")) -> float:
    if value in (None, ""):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return parsed if math.isfinite(parsed) else default


def load_survival_best_run_ids(path: Path) -> set[str]:
    if not path.exists():
        return set()
    best: dict[str, tuple[BestRunKey, str]] = {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("run_type") == "non_llm":
                continue
            model = row.get("model") or ""
            run_id = row.get("run_id") or ""
            key = BestRunKey(
                run_days=finite_float(row.get("run_days")),
                final_networth=finite_float(row.get("final_networth")),
                total_sales=finite_float(row.get("total_sales")),
            )
            if model and run_id and (model not in best or key > best[model][0]):
                best[model] = (key, run_id)
    return {run_id for _, run_id in best.values()}


def load_metric_summary(path: Path, run_ids: set[str] | None = None) -> dict[str, Any]:
    rows = []
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("run_type") == "non_llm":
                continue
            if run_ids is not None and row.get("run_id") not in run_ids:
                continue
            rows.append(row)

    def weighted_mean(column: str) -> float | None:
        total_weight = 0.0
        total_value = 0.0
        for row in rows:
            value = finite_float(row.get(column), default=float("nan"))
            if not math.isfinite(value):
                continue
            weight = finite_float(row.get("s3_place_order_eval_count"), default=1.0)
            if not math.isfinite(weight) or weight <= 0:
                weight = 1.0
            total_weight += weight
            total_value += value * weight
        return total_value / total_weight if total_weight else None

    return {
        "runs": len(rows),
        "order_lines": int(sum(max(finite_float(row.get("s3_place_order_eval_count"), 0.0), 0.0) for row in rows)),
        "quality_first_rate": weighted_mean("s3_supplier_quality_first_rate"),
        "price_first_rate": weighted_mean("s3_supplier_price_first_rate"),
        "avg_quality_rank": weighted_mean("s3_supplier_quality_rank_mean"),
        "avg_price_rank": weighted_mean("s3_supplier_price_rank_mean"),
    }


def load_metric_rows_by_run(path: Path, run_ids: set[str]) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            run_id = row.get("run_id") or ""
            if run_id not in run_ids:
                continue
            rows[run_id] = {
                "model": row.get("model") or "",
                "framework": row.get("framework") or "",
                "run_id": run_id,
                "is_survival_best": True,
                "order_lines": int(max(finite_float(row.get("s3_place_order_eval_count"), 0.0), 0.0)),
                "quality_first_rate": finite_float(row.get("s3_supplier_quality_first_rate"), default=float("nan")),
                "price_first_rate": finite_float(row.get("s3_supplier_price_first_rate"), default=float("nan")),
                "avg_quality_rank": finite_float(row.get("s3_supplier_quality_rank_mean"), default=float("nan")),
                "avg_price_rank": finite_float(row.get("s3_supplier_price_rank_mean"), default=float("nan")),
            }
    return rows


def iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def args_include_sku(args: dict[str, Any], sku_id: str) -> bool:
    for key in ("sku_id", "sku"):
        if str(args.get(key) or "") == sku_id:
            return True
    for key in ("sku_ids", "_dynamic_sku_ids"):
        values = [str(item) for item in as_list(args.get(key))]
        if sku_id in values:
            return True
    return not any(key in args for key in ("sku_id", "sku", "sku_ids", "_dynamic_sku_ids"))


def args_include_supplier(args: dict[str, Any], supplier_id: str) -> bool:
    raw_supplier = args.get("supplier_id")
    return raw_supplier in (None, "") or str(raw_supplier) == supplier_id


def code_text(record: dict[str, Any]) -> str:
    if record.get("tool") != "execute_code":
        return ""
    args = record.get("args") or {}
    return str(args.get("code") or "")


def summarize_pre_action_context(
    day_records: list[dict[str, Any]],
    sku_id: str,
    supplier_id: str,
) -> dict[str, Any]:
    relevant_tools: Counter[str] = Counter()
    quality_proxy_tools: Counter[str] = Counter()
    price_tools: Counter[str] = Counter()
    execute_code_quality_mentions = 0
    execute_code_price_priority_mentions = 0
    execute_code_return_mentions = 0
    snippets: list[str] = []

    for item in day_records:
        tool = str(item.get("tool") or "")
        args = item.get("args") or {}
        if args_include_sku(args, sku_id):
            relevant_tools[tool] += 1
            if tool in QUALITY_PROXY_TOOLS and args_include_supplier(args, supplier_id):
                quality_proxy_tools[tool] += 1
            if tool in PRICE_TOOLS:
                price_tools[tool] += 1

        code = code_text(item)
        if not code:
            continue
        lowered = code.lower()
        if sku_id in code:
            if any(token in lowered for token in ("quality", "return", "rating")):
                execute_code_quality_mentions += 1
                if len(snippets) < 2:
                    snippets.append(shorten_code(code))
            if any(token in lowered for token in ("cheapest", "lowest", "min(", "< best['price']", "< best[\"price\"]")):
                execute_code_price_priority_mentions += 1
                if len(snippets) < 2:
                    snippets.append(shorten_code(code))
            if "return" in lowered:
                execute_code_return_mentions += 1

    return {
        "relevant_tools": dict(relevant_tools),
        "price_tool_count": sum(price_tools.values()),
        "quality_proxy_tool_count": sum(quality_proxy_tools.values()),
        "quality_proxy_tools": dict(quality_proxy_tools),
        "had_supplier_price_query": bool(price_tools.get("view_current_date_supplier_prices")),
        "had_supplier_price_history": bool(price_tools.get("view_supplier_price_history")),
        "had_quality_proxy_query": bool(quality_proxy_tools),
        "had_return_rate_query": bool(quality_proxy_tools.get("view_supplier_returns_avg_rate")),
        "had_rating_query": bool(quality_proxy_tools.get("view_sku_avg_ratings")),
        "execute_code_quality_mentions": execute_code_quality_mentions,
        "execute_code_price_priority_mentions": execute_code_price_priority_mentions,
        "execute_code_return_mentions": execute_code_return_mentions,
        "snippets": snippets,
    }


def shorten_code(code: str, limit: int = 360) -> str:
    compact = " ".join(line.strip() for line in code.splitlines() if line.strip())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def build_candidate_table(info: dict[str, Any]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for supplier_id, price in info["price_by_supplier"].items():
        candidates.append(
            {
                "supplier_id": supplier_id,
                "price": round(float(price), 4),
                "quality_score": round(float(info["quality_by_supplier"].get(supplier_id)), 6)
                if info["quality_by_supplier"].get(supplier_id) is not None
                else None,
                "price_rank": info["price_rank"].get(supplier_id),
                "quality_rank": info["quality_rank"].get(supplier_id),
            }
        )
    return sorted(
        candidates,
        key=lambda item: (
            item["quality_rank"] if item["quality_rank"] is not None else 999,
            item["price_rank"] if item["price_rank"] is not None else 999,
        ),
    )


class LazySupplierStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._path_index: dict[str, list[Path]] | None = None
        self._sku_cache: dict[str, list[dict[str, Any]]] = {}

    def _build_index(self) -> dict[str, list[Path]]:
        if self._path_index is not None:
            return self._path_index
        index: dict[str, list[Path]] = defaultdict(list)
        for path in self.root.rglob("*_suppliers.json"):
            sku_id = path.stem.split("_suppliers")[0]
            index[sku_id].append(path)
        self._path_index = dict(index)
        return self._path_index

    def _load_sku_rows(self, sku_id: str) -> list[dict[str, Any]]:
        if sku_id in self._sku_cache:
            return self._sku_cache[sku_id]
        rows: list[dict[str, Any]] = []
        for path in self._build_index().get(sku_id, []):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            for supplier_id, supplier_rows in payload.items():
                if not isinstance(supplier_rows, list):
                    continue
                for raw in supplier_rows:
                    if isinstance(raw, dict):
                        rows.append({"supplier_id": str(supplier_id), "raw": raw})
        self._sku_cache[sku_id] = rows
        return rows

    def get_sku_date(self, sku_id: str, current_date: str) -> list[dict[str, Any]]:
        entries = []
        for item in self._load_sku_rows(sku_id):
            raw = item["raw"]
            if normalize_date(raw.get("date")) != current_date:
                continue
            price = raw.get("supplier_price") or raw.get("price") or raw.get("base_cost_price")
            try:
                price_f = float(price)
            except (TypeError, ValueError):
                continue
            entries.append(
                {
                    "supplier_id": item["supplier_id"],
                    "sku_id": raw.get("upc") or raw.get("sku_id") or sku_id,
                    "date": current_date,
                    "price": price_f,
                    "raw": raw,
                }
            )
        return sorted(entries, key=lambda row: row["supplier_id"])


def normalize_date(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value)
    for fmt in ("%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def supplier_store_for_run(run_dir: Path) -> LazySupplierStore | None:
    cfg = _load_run_config(run_dir)
    store_root = _resolve_repo_path(cfg.get("supplier_data_dir") or cfg.get("data_dir"))
    store_id = cfg.get("store_id")
    if not store_root or not store_id:
        return None
    root = store_root / str(store_id)
    return LazySupplierStore(root) if root.exists() else None


def supplier_rank_info_from_store(supplier_store: LazySupplierStore, sku_id: str, current_date: str) -> dict[str, Any]:
    quotes = []
    for entry in supplier_store.get_sku_date(sku_id, current_date) or []:
        price = entry.get("price")
        if not isinstance(price, (int, float)):
            continue
        supplier_id = str(entry.get("supplier_id") or "")
        if not supplier_id:
            continue
        quotes.append(
            {
                "supplier_id": supplier_id,
                "sku_id": sku_id,
                "price": float(price),
                "quality_score": parse_entry_quality(entry),
            }
        )

    price_sorted = sorted(quotes, key=lambda q: (q["price"], q["supplier_id"]))
    price_rank = {q["supplier_id"]: i + 1 for i, q in enumerate(price_sorted)}
    price_by_supplier = {q["supplier_id"]: float(q["price"]) for q in quotes}
    quality_by_supplier = {q["supplier_id"]: q.get("quality_score") for q in quotes}
    quality_quotes = [
        {**q, "quality_score": float(quality_by_supplier[q["supplier_id"]])}
        for q in quotes
        if quality_by_supplier.get(q["supplier_id"]) is not None
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


def parse_entry_quality(entry: dict[str, Any]) -> float | None:
    raw = entry.get("raw") if isinstance(entry.get("raw"), dict) else entry
    for key in ("quality_score", "quality", "qualityScore"):
        value = raw.get(key)
        if value in (None, ""):
            continue
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            continue
        return parsed if math.isfinite(parsed) else None
    return None


def analyze_run(
    run: RunSpec,
    is_survival_best: bool,
    max_lines: int | None = MAX_SAMPLE_LINES_PER_RUN,
) -> dict[str, Any]:
    run_dir = run.source_path
    tool_calls_path = run_dir / "tool_calls.jsonl"
    if run.run_type == "non_llm" or not tool_calls_path.exists():
        return {
            "model": run.model,
            "framework": run.framework,
            "run_id": run.run_id,
            "run_type": run.run_type,
            "is_survival_best": is_survival_best,
            "skipped": "missing_tool_calls",
        }

    supplier_store = supplier_store_for_run(run_dir)
    if supplier_store is None:
        return {
            "model": run.model,
            "framework": run.framework,
            "run_id": run.run_id,
            "run_type": run.run_type,
            "is_survival_best": is_survival_best,
            "skipped": "missing_supplier_store",
        }
    rank_cache: dict[tuple[str, str], dict[str, Any]] = {}
    day_records: list[dict[str, Any]] = []
    line_rows: list[dict[str, Any]] = []

    try:
        for record in iter_jsonl(tool_calls_path):
            tool = str(record.get("tool") or "")
            if tool == "end_today":
                day_records = []
                continue

            if tool == "place_order" and _is_executed_business_action(record):
                args = record.get("args") or {}
                supplier_id = str(args.get("supplier_id") or "")
                current_date = str(record.get("current_date") or "")
                context_by_key: dict[tuple[str, str], dict[str, Any]] = {}
                for line in _successful_order_lines(record):
                    sku_id = str(line.get("sku_id") or "")
                    if not sku_id or not supplier_id or not current_date:
                        continue
                    key = (sku_id, current_date)
                    info = rank_cache.setdefault(
                        key,
                        supplier_rank_info_from_store(supplier_store, sku_id, current_date),
                    )
                    price_rank = info["price_rank"].get(supplier_id)
                    quality_rank = info["quality_rank"].get(supplier_id)
                    if price_rank is None or quality_rank is None:
                        continue
                    context = context_by_key.setdefault(
                        (sku_id, supplier_id),
                        summarize_pre_action_context(day_records, sku_id, supplier_id),
                    )
                    selected_quality = info["quality_by_supplier"].get(supplier_id)
                    best_quality = info["best_quality"]
                    best_quality_supplier = best_quality.get("supplier_id") if best_quality else None
                    best_quality_score = best_quality.get("quality_score") if best_quality else None
                    line_rows.append(
                        {
                            "model": run.model,
                            "framework": run.framework,
                            "run_id": run.run_id,
                            "is_survival_best": is_survival_best,
                            "current_date": current_date,
                            "sku_id": sku_id,
                            "selected_supplier": supplier_id,
                            "quantity": line.get("quantity"),
                            "candidate_count": info["candidate_count"],
                            "selected_price_rank": int(price_rank),
                            "selected_quality_rank": int(quality_rank),
                            "selected_quality_score": round(float(selected_quality), 6)
                            if selected_quality is not None
                            else None,
                            "best_quality_supplier": best_quality_supplier,
                            "best_quality_score": round(float(best_quality_score), 6)
                            if best_quality_score is not None
                            else None,
                            "selected_is_cheapest": price_rank == 1,
                            "selected_is_quality_best": quality_rank == 1,
                            "pre_action_context": context,
                            "candidates": build_candidate_table(info),
                        }
                    )

            day_records.append(record)
            if max_lines is not None and len(line_rows) >= max_lines:
                break
    finally:
        pass

    return {
        "model": run.model,
        "framework": run.framework,
        "run_id": run.run_id,
        "run_type": run.run_type,
        "is_survival_best": is_survival_best,
        "line_rows": line_rows,
    }


def average(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def pct(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def summarize_lines(lines: list[dict[str, Any]]) -> dict[str, Any]:
    total = len(lines)
    non_quality = [row for row in lines if not row["selected_is_quality_best"]]
    return {
        "order_lines": total,
        "quality_first_rate": pct(sum(row["selected_is_quality_best"] for row in lines), total),
        "price_first_rate": pct(sum(row["selected_is_cheapest"] for row in lines), total),
        "avg_quality_rank": average([float(row["selected_quality_rank"]) for row in lines]),
        "avg_price_rank": average([float(row["selected_price_rank"]) for row in lines]),
        "non_quality_first_lines": len(non_quality),
        "non_quality_first_price_first_rate": pct(
            sum(row["selected_is_cheapest"] for row in non_quality),
            len(non_quality),
        ),
        "supplier_price_query_rate": pct(
            sum(row["pre_action_context"]["had_supplier_price_query"] for row in lines),
            total,
        ),
        "quality_proxy_query_rate": pct(
            sum(row["pre_action_context"]["had_quality_proxy_query"] for row in lines),
            total,
        ),
        "return_rate_query_rate": pct(
            sum(row["pre_action_context"]["had_return_rate_query"] for row in lines),
            total,
        ),
        "rating_query_rate": pct(
            sum(row["pre_action_context"]["had_rating_query"] for row in lines),
            total,
        ),
        "execute_code_quality_mention_rate": pct(
            sum(row["pre_action_context"]["execute_code_quality_mentions"] > 0 for row in lines),
            total,
        ),
        "execute_code_price_priority_mention_rate": pct(
            sum(row["pre_action_context"]["execute_code_price_priority_mentions"] > 0 for row in lines),
            total,
        ),
    }


def summarize_by_run(run_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for item in run_results:
        lines = item.get("line_rows") or []
        summary = summarize_lines(lines)
        rows.append(
            {
                "model": item["model"],
                "framework": item["framework"],
                "run_id": item["run_id"],
                "is_survival_best": item["is_survival_best"],
                **summary,
            }
        )
    return rows


def choose_example_failures(lines: list[dict[str, Any]], limit: int = 12) -> list[dict[str, Any]]:
    failures = [row for row in lines if not row["selected_is_quality_best"]]
    failures.sort(
        key=lambda row: (
            row["is_survival_best"],
            row["selected_is_cheapest"],
            row["selected_quality_rank"],
            row["candidate_count"],
        ),
        reverse=True,
    )
    examples = []
    seen_models: Counter[str] = Counter()
    seen_cases: set[tuple[str, str, str, str]] = set()
    for row in failures:
        case_key = (
            row["run_id"],
            row["current_date"],
            row["sku_id"],
            row["selected_supplier"],
        )
        if case_key in seen_cases:
            continue
        if seen_models[row["model"]] >= 2:
            continue
        seen_cases.add(case_key)
        seen_models[row["model"]] += 1
        examples.append(row)
        if len(examples) >= limit:
            break
    return examples


def main() -> None:
    _, runs = load_manifest(SCRIPT_DIR / "manifest.json")
    best_run_ids = load_survival_best_run_ids(METRICS_CSV)
    run_results = []
    for run in runs:
        if run.run_type == "non_llm" or run.run_id not in best_run_ids:
            continue
        print(f"Auditing {run.model} / {run.framework} ...", flush=True)
        run_results.append(analyze_run(run, True))

    best_lines = [
        row
        for result in run_results
        for row in result.get("line_rows", [])
    ]
    best_summary = load_metric_summary(METRICS_CSV, best_run_ids)
    best_sample_summary = summarize_lines(best_lines)
    for key in (
        "non_quality_first_lines",
        "non_quality_first_price_first_rate",
        "supplier_price_query_rate",
        "quality_proxy_query_rate",
        "return_rate_query_rate",
        "rating_query_rate",
        "execute_code_quality_mention_rate",
        "execute_code_price_priority_mention_rate",
    ):
        best_summary[key] = best_sample_summary.get(key)
    sample_by_run = {row["run_id"]: row for row in summarize_by_run(run_results)}
    metric_by_run = load_metric_rows_by_run(METRICS_CSV, best_run_ids)
    merged_by_run = []
    for run_id, metric_row in metric_by_run.items():
        sample_row = sample_by_run.get(run_id, {})
        metric_row["quality_proxy_query_rate"] = sample_row.get("quality_proxy_query_rate")
        metric_row["sample_order_lines"] = sample_row.get("order_lines", 0)
        merged_by_run.append(metric_row)
    payload = {
        "max_sample_lines_per_run": MAX_SAMPLE_LINES_PER_RUN,
        "summary": {
            "all_llm": load_metric_summary(METRICS_CSV),
            "survival_best_llm": best_summary,
        },
        "by_run": merged_by_run,
        "example_failures": choose_example_failures(best_lines),
    }
    DETAILS_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    REPORT_MD.write_text(render_report(payload), encoding="utf-8")
    print(f"Wrote {DETAILS_JSON}")
    print(f"Wrote {REPORT_MD}")


if __name__ == "__main__":
    main()
