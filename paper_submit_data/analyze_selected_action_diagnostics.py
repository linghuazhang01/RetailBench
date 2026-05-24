#!/usr/bin/env python3
"""Regenerate selected-run action diagnostics from the paper-submit manifest."""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from analyze_metrics import RunSpec, load_manifest  # noqa: E402
from analysis.evaluate_final_metrics import compute_final_run_metrics  # noqa: E402


LLM_COLUMNS = [
    "model",
    "framework",
    "run_id",
    "run_days",
    "final_networth",
    "total_sales",
    "avg_daily_sold_skus",
    "irrational_action",
    "stockout_days",
    "query_depth",
    "action_correction",
    "strategy_consistency",
    "raw_quality_top1_hit",
    "quality_regret_mean",
    "quality_ratio_mean",
    "price_first_top1_hit",
    "price_regret_mean",
    "price_premium_mean",
    "order_qty_to_avg_daily_sales_mean",
    "modify_price_distance_pct_mean",
    "modify_price_distance_pct_max",
    "modify_price_min",
    "modify_price_max",
]

ALL_COLUMNS = ["run_type", "source_path", *LLM_COLUMNS]


def read_selected_run_ids(path: Path) -> set[str]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return {row["run_id"] for row in csv.DictReader(handle)}


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in columns})


def analyze_spec(spec: RunSpec, max_days: int | None) -> dict[str, Any] | None:
    if spec.source_format == "summary_log" or not (spec.source_path / "tool_calls.jsonl").exists():
        return None
    row = compute_final_run_metrics(
        spec.source_path,
        spec.framework,
        spec.model,
        scenario="hard_v2",
        max_days=max_days,
    )
    if row is None:
        return None
    row["run_id"] = spec.run_id
    row["run_type"] = spec.run_type
    row["source_path"] = str(spec.source_path)
    return row


def main() -> None:
    parser = argparse.ArgumentParser(description="Regenerate selected action diagnostics.")
    parser.add_argument("--manifest", type=Path, default=SCRIPT_DIR / "manifest.json")
    parser.add_argument("--output-dir", type=Path, default=SCRIPT_DIR / "outputs")
    parser.add_argument("--max-days", type=int, default=None)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    best_path = args.output_dir / "best_framework_by_model.csv"
    selected_ids = read_selected_run_ids(best_path)
    _, specs = load_manifest(args.manifest)

    all_rows: list[dict[str, Any]] = []
    for spec in specs:
        if spec.run_id not in selected_ids:
            continue
        print(f"Analyzing {spec.model} / {spec.framework} ...", flush=True)
        row = analyze_spec(spec, args.max_days)
        if row is not None:
            all_rows.append(row)

    llm_rows = [row for row in all_rows if row.get("run_type") == "llm"]
    write_csv(args.output_dir / "selected_llm_action_diagnostics.csv", llm_rows, LLM_COLUMNS)
    write_csv(args.output_dir / "selected_action_diagnostics.csv", all_rows, ALL_COLUMNS)
    (args.output_dir / "selected_llm_action_diagnostics.json").write_text(
        json.dumps(llm_rows, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    (args.output_dir / "selected_action_diagnostics.json").write_text(
        json.dumps(all_rows, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"Wrote {len(llm_rows)} LLM rows and {len(all_rows)} selected rows")


if __name__ == "__main__":
    main()
