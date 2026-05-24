#!/usr/bin/env python3
"""Compare a Stage-3 mechanism run against the DeepSeek first-50-day baseline."""
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

from analyze_four_stage_metrics import analyze_spec  # noqa: E402
from analyze_metrics import RunSpec, analyze_run, load_manifest  # noqa: E402


DEFAULT_BASELINE_RUN_ID = "run_plan_and_act_deepseek-v4-pro_caec_off_fcr_off_run1"
DEFAULT_OUTPUT_DIR = SCRIPT_DIR / "outputs" / "stage3_mechanism_comparison"
COMPARE_COLUMNS = [
    "run_label",
    "model",
    "framework",
    "run_days",
    "final_networth",
    "total_sales",
    "return_ratio",
    "stockout_ratio",
    "s2_place_order_query_depth",
    "s3_action_correction",
    "s3_supplier_quality_first_rate",
    "s3_supplier_price_first_rate",
    "s3_supplier_quality_rank_mean",
    "s3_supplier_price_rank_mean",
    "s3_modify_price_distance_pct_mean",
]


def find_manifest_spec(manifest_path: Path, run_id: str) -> RunSpec:
    _, specs = load_manifest(manifest_path)
    for spec in specs:
        if spec.run_id == run_id:
            return spec
    raise ValueError(f"Run id not found in manifest: {run_id}")


def candidate_spec(run_dir: Path, run_id: str, label: str) -> RunSpec:
    return RunSpec(
        model="DeepSeek-V4-Pro",
        model_slug="deepseek_v4_pro",
        framework="plan_and_act",
        run_id=run_id or label,
        source_path=run_dir,
        run_type="llm",
        source_format="tool_calls",
        database_path=None,
        notes="Stage-3 quality-aware supplier ranking scaffold candidate run.",
    )


def evaluate_spec(spec: RunSpec, label: str, max_days: int) -> dict[str, Any]:
    base = analyze_run(
        spec,
        max_days=max_days,
        input_cost_per_mtok=0.0,
        output_cost_per_mtok=0.0,
    )
    row = analyze_spec(spec, {spec.run_id: base}, max_days=max_days)
    row["run_label"] = label
    row["source_path"] = str(spec.source_path)
    return row


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = list(COMPARE_COLUMNS)
    extras = sorted({key for row in rows for key in row if key not in fieldnames})
    fieldnames.extend(extras)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def fmt(value: Any, digits: int = 4) -> str:
    if value in (None, ""):
        return "--"
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return str(value)
    if abs(parsed - round(parsed)) < 1e-9:
        return str(int(round(parsed)))
    return f"{parsed:.{digits}f}"


def delta(candidate: dict[str, Any], baseline: dict[str, Any], key: str) -> str:
    try:
        return fmt(float(candidate.get(key)) - float(baseline.get(key)))
    except (TypeError, ValueError):
        return "--"


def render_report(rows: list[dict[str, Any]], max_days: int) -> str:
    baseline = rows[0]
    lines = [
        "# Stage 3 Mechanism Comparison",
        "",
        f"Window: first {max_days} days.",
        "",
        "## Runs",
        "",
        "| Label | Source |",
        "|---|---|",
    ]
    for row in rows:
        lines.append(f"| {row['run_label']} | `{row['source_path']}` |")

    lines.extend(
        [
            "",
            "## Metrics",
            "",
            "| Label | Days | NetWorth | Sales | QFirst | PriceFirst | QRank | PriceRank | ActionCorr | OrderQDepth |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in rows:
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["run_label"]),
                    fmt(row.get("run_days")),
                    fmt(row.get("final_networth")),
                    fmt(row.get("total_sales")),
                    fmt(row.get("s3_supplier_quality_first_rate")),
                    fmt(row.get("s3_supplier_price_first_rate")),
                    fmt(row.get("s3_supplier_quality_rank_mean")),
                    fmt(row.get("s3_supplier_price_rank_mean")),
                    fmt(row.get("s3_action_correction")),
                    fmt(row.get("s2_place_order_query_depth")),
                ]
            )
            + " |"
        )

    if len(rows) > 1:
        candidate = rows[1]
        lines.extend(
            [
                "",
                "## Candidate Minus Baseline",
                "",
                "| Metric | Delta | Desired Direction |",
                "|---|---:|---|",
                f"| QualityFirst | {delta(candidate, baseline, 's3_supplier_quality_first_rate')} | higher |",
                f"| PriceFirst | {delta(candidate, baseline, 's3_supplier_price_first_rate')} | lower |",
                f"| Mean quality rank | {delta(candidate, baseline, 's3_supplier_quality_rank_mean')} | lower |",
                f"| Action correction | {delta(candidate, baseline, 's3_action_correction')} | non-decreasing |",
                f"| Final networth | {delta(candidate, baseline, 'final_networth')} | non-collapsing |",
            ]
        )
    else:
        lines.extend(
            [
                "",
                "## Candidate Minus Baseline",
                "",
                "Candidate run not provided yet. Re-run this script with `--candidate-run-dir` after the 50-day experiment finishes.",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Compare Stage-3 mechanism run against baseline.")
    parser.add_argument("--manifest", type=Path, default=SCRIPT_DIR / "manifest.json")
    parser.add_argument("--baseline-run-id", type=str, default=DEFAULT_BASELINE_RUN_ID)
    parser.add_argument("--candidate-run-dir", type=Path, default=None)
    parser.add_argument("--candidate-run-id", type=str, default="stage3_quality_scaffold_deepseek50")
    parser.add_argument("--max-days", type=int, default=50)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    baseline_spec = find_manifest_spec(args.manifest, args.baseline_run_id)
    rows = [evaluate_spec(baseline_spec, "baseline_deepseek_plan_first50", args.max_days)]
    if args.candidate_run_dir:
        rows.append(
            evaluate_spec(
                candidate_spec(args.candidate_run_dir, args.candidate_run_id, "quality_scaffold_candidate"),
                "quality_scaffold_candidate",
                args.max_days,
            )
        )

    csv_path = args.output_dir / "stage3_mechanism_comparison.csv"
    json_path = args.output_dir / "stage3_mechanism_comparison.json"
    report_path = args.output_dir / "stage3_mechanism_comparison.md"
    write_csv(csv_path, rows)
    json_path.write_text(json.dumps({"max_days": args.max_days, "rows": rows}, indent=2), encoding="utf-8")
    report_path.write_text(render_report(rows, args.max_days), encoding="utf-8")
    print(f"Wrote {csv_path}")
    print(f"Wrote {json_path}")
    print(f"Wrote {report_path}")


if __name__ == "__main__":
    main()
