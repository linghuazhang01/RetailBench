#!/usr/bin/env python3
"""Analyze supplier quality ranks in history SQL and first model orders."""
from __future__ import annotations

import csv
import json
import math
import sqlite3
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from analyze_metrics import RunSpec, load_manifest  # noqa: E402
from analyze_supplier_quality_failures import load_survival_best_run_ids  # noqa: E402
from four_stage_action_metrics import (  # noqa: E402
    iter_action_records,
    supplier_rank_info,
)
from analysis.evaluate_final_metrics import (  # noqa: E402
    _final_db_path,
    _is_executed_business_action,
    _successful_order_lines,
    _supplier_manager_for_run,
)


OUTPUT_DIR = SCRIPT_DIR / "outputs"
METRICS_CSV = OUTPUT_DIR / "four_stage_metrics.csv"
SQL_DUMP = REPO_ROOT / "data" / "dynamic" / "simulate_data" / "15" / "records.sql"
SQLITE_CACHE = REPO_ROOT / "temp" / "store15_records_from_sql.db"
SUMMARY_JSON = OUTPUT_DIR / "supplier_rank_history_first_order_analysis.json"
REPORT_MD = OUTPUT_DIR / "supplier_rank_history_first_order_analysis.md"
HISTORY_SKU_CSV = OUTPUT_DIR / "historical_supplier_rank_by_sku.csv"
FIRST_ORDER_RUN_CSV = OUTPUT_DIR / "first_order_supplier_rank_by_run.csv"
FIRST_ORDER_LINES_CSV = OUTPUT_DIR / "first_order_supplier_rank_lines.csv"


@dataclass(frozen=True)
class RankRecord:
    sku_id: str
    supplier_id: str
    current_date: str
    quantity: int
    quality_rank: int | None
    price_rank: int | None
    candidate_count: int
    quality_score: float | None
    best_quality_supplier: str | None
    best_quality_score: float | None
    is_quality_best: bool | None
    is_price_best: bool | None


def finite(value: Any) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def avg(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def weighted_avg(items: list[tuple[float, float]]) -> float | None:
    total_weight = sum(weight for _, weight in items if weight > 0)
    if total_weight <= 0:
        return None
    return sum(value * weight for value, weight in items if weight > 0) / total_weight


def rate(values: list[bool]) -> float | None:
    return sum(values) / len(values) if values else None


def pct(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def fmt(value: float | None) -> str:
    return "N/A" if value is None else f"{value:.3f}"


def ensure_sqlite_cache() -> Path:
    SQLITE_CACHE.parent.mkdir(parents=True, exist_ok=True)
    if SQLITE_CACHE.exists() and SQLITE_CACHE.stat().st_mtime >= SQL_DUMP.stat().st_mtime:
        return SQLITE_CACHE
    if SQLITE_CACHE.exists():
        SQLITE_CACHE.unlink()
    with SQL_DUMP.open("rb") as handle:
        subprocess.run(
            ["sqlite3", str(SQLITE_CACHE)],
            stdin=handle,
            check=True,
        )
    return SQLITE_CACHE


def rank_record_for(
    conn: sqlite3.Connection,
    supplier_manager: Any,
    *,
    sku_id: str,
    supplier_id: str,
    current_date: str,
    quantity: int,
    rank_cache: dict[tuple[str, str], dict[str, Any]],
) -> RankRecord | None:
    key = (sku_id, current_date)
    info = rank_cache.setdefault(
        key,
        supplier_rank_info(conn, supplier_manager, sku_id, current_date),
    )
    quality_rank = info["quality_rank"].get(supplier_id)
    price_rank = info["price_rank"].get(supplier_id)
    if quality_rank is None and price_rank is None:
        return None
    selected_quality = info["quality_by_supplier"].get(supplier_id)
    best_quality = info.get("best_quality") or {}
    best_supplier = best_quality.get("supplier_id")
    best_score = finite(best_quality.get("quality_score"))
    return RankRecord(
        sku_id=sku_id,
        supplier_id=supplier_id,
        current_date=current_date,
        quantity=int(quantity),
        quality_rank=int(quality_rank) if quality_rank is not None else None,
        price_rank=int(price_rank) if price_rank is not None else None,
        candidate_count=int(info.get("candidate_count") or 0),
        quality_score=finite(selected_quality),
        best_quality_supplier=str(best_supplier) if best_supplier else None,
        best_quality_score=best_score,
        is_quality_best=quality_rank == 1 if quality_rank is not None else None,
        is_price_best=price_rank == 1 if price_rank is not None else None,
    )


def summarize_rank_records(records: list[RankRecord]) -> dict[str, Any]:
    q_ranks = [float(item.quality_rank) for item in records if item.quality_rank is not None]
    p_ranks = [float(item.price_rank) for item in records if item.price_rank is not None]
    q_best = [bool(item.is_quality_best) for item in records if item.is_quality_best is not None]
    p_best = [bool(item.is_price_best) for item in records if item.is_price_best is not None]
    q_weighted = [
        (float(item.quality_rank), float(item.quantity))
        for item in records
        if item.quality_rank is not None and item.quantity > 0
    ]
    p_weighted = [
        (float(item.price_rank), float(item.quantity))
        for item in records
        if item.price_rank is not None and item.quantity > 0
    ]
    q_best_weighted = [
        (1.0 if item.is_quality_best else 0.0, float(item.quantity))
        for item in records
        if item.is_quality_best is not None and item.quantity > 0
    ]
    p_best_weighted = [
        (1.0 if item.is_price_best else 0.0, float(item.quantity))
        for item in records
        if item.is_price_best is not None and item.quantity > 0
    ]
    return {
        "records": len(records),
        "total_units": sum(item.quantity for item in records),
        "distinct_skus": len({item.sku_id for item in records}),
        "distinct_suppliers": len({item.supplier_id for item in records}),
        "quality_rank_mean": avg(q_ranks),
        "price_rank_mean": avg(p_ranks),
        "quality_first_rate": rate(q_best),
        "price_first_rate": rate(p_best),
        "unit_weighted_quality_rank_mean": weighted_avg(q_weighted),
        "unit_weighted_price_rank_mean": weighted_avg(p_weighted),
        "unit_weighted_quality_first_rate": weighted_avg(q_best_weighted),
        "unit_weighted_price_first_rate": weighted_avg(p_best_weighted),
    }


def analyze_history_sql() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    db_path = ensure_sqlite_cache()
    conn = sqlite3.connect(str(db_path))
    supplier_manager = _supplier_manager_for_run(
        Path(
            "/Volumes/Data/logs/batches_final_hard_v2/"
            "hard_v2_all_2026-05-20_19-39-54_react_MiniMax-M2.5_caec_off_fcr_off/"
            "run_react_MiniMax-M2.5_caec_off_fcr_off_run1"
        )
    )
    if supplier_manager is None:
        raise RuntimeError("Unable to load supplier manager for store 15.")

    rank_cache: dict[tuple[str, str], dict[str, Any]] = {}
    line_records: list[RankRecord] = []
    by_sku_records: dict[str, list[RankRecord]] = defaultdict(list)

    rows = conn.execute(
        """
        SELECT order_id, sku_id, supplier_id, order_date, COUNT(*) AS quantity
        FROM product_lifecycle
        WHERE order_id LIKE 'hist-%'
        GROUP BY order_id, sku_id, supplier_id, order_date
        ORDER BY order_date, order_id, sku_id
        """
    ).fetchall()
    for _, sku_id, supplier_id, order_date, quantity in rows:
        item = rank_record_for(
            conn,
            supplier_manager,
            sku_id=str(sku_id),
            supplier_id=str(supplier_id),
            current_date=str(order_date),
            quantity=int(quantity or 0),
            rank_cache=rank_cache,
        )
        if item is None:
            continue
        line_records.append(item)
        by_sku_records[item.sku_id].append(item)

    sku_rows: list[dict[str, Any]] = []
    for sku_id, sku_records in sorted(by_sku_records.items()):
        summary = summarize_rank_records(sku_records)
        supplier_units: dict[str, int] = defaultdict(int)
        for item in sku_records:
            supplier_units[item.supplier_id] += item.quantity
        dominant_supplier, dominant_units = max(
            supplier_units.items(),
            key=lambda pair: (pair[1], pair[0]),
        )
        dominant_rows = [item for item in sku_records if item.supplier_id == dominant_supplier]
        sku_rows.append(
            {
                "sku_id": sku_id,
                "history_lines": summary["records"],
                "history_units": summary["total_units"],
                "quality_rank_mean": summary["quality_rank_mean"],
                "unit_weighted_quality_rank_mean": summary["unit_weighted_quality_rank_mean"],
                "quality_first_rate": summary["quality_first_rate"],
                "unit_weighted_quality_first_rate": summary["unit_weighted_quality_first_rate"],
                "price_first_rate": summary["price_first_rate"],
                "dominant_supplier": dominant_supplier,
                "dominant_supplier_units": dominant_units,
                "dominant_supplier_unit_share": dominant_units / summary["total_units"]
                if summary["total_units"]
                else None,
                "dominant_supplier_quality_rank_mean": avg(
                    [float(item.quality_rank) for item in dominant_rows if item.quality_rank is not None]
                ),
            }
        )

    overall = summarize_rank_records(line_records)
    overall["source_sql"] = str(SQL_DUMP)
    overall["sqlite_cache"] = str(db_path)
    overall["history_line_count"] = len(line_records)
    conn.close()
    return overall, sku_rows


def analyze_first_orders() -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    _, runs = load_manifest(SCRIPT_DIR / "manifest.json")
    best_run_ids = load_survival_best_run_ids(METRICS_CSV)
    line_rows: list[dict[str, Any]] = []
    run_rows: list[dict[str, Any]] = []

    for run in runs:
        if run.run_type == "non_llm":
            continue
        run_dir = run.source_path
        db_path = _final_db_path(run_dir)
        if not db_path or not db_path.exists() or not (run_dir / "tool_calls.jsonl").exists():
            continue
        supplier_manager = _supplier_manager_for_run(run_dir)
        if supplier_manager is None:
            continue
        conn = sqlite3.connect(str(db_path))
        rank_cache: dict[tuple[str, str], dict[str, Any]] = {}
        seen_skus: set[str] = set()
        first_records: list[RankRecord] = []
        try:
            for record in iter_action_records(run_dir / "tool_calls.jsonl"):
                if record.get("tool") != "place_order" or not _is_executed_business_action(record):
                    continue
                args = record.get("args") or {}
                supplier_id = str(args.get("supplier_id") or "")
                current_date = str(record.get("current_date") or "")
                if not supplier_id or not current_date:
                    continue
                for line in _successful_order_lines(record):
                    sku_id = str(line.get("sku_id") or "")
                    if not sku_id or sku_id in seen_skus:
                        continue
                    quantity = int(line.get("quantity") or 0)
                    item = rank_record_for(
                        conn,
                        supplier_manager,
                        sku_id=sku_id,
                        supplier_id=supplier_id,
                        current_date=current_date,
                        quantity=quantity,
                        rank_cache=rank_cache,
                    )
                    if item is None:
                        continue
                    seen_skus.add(sku_id)
                    first_records.append(item)
                    line_rows.append(
                        {
                            "model": run.model,
                            "framework": run.framework,
                            "run_id": run.run_id,
                            "is_survival_best": run.run_id in best_run_ids,
                            "first_order_date": item.current_date,
                            "sku_id": item.sku_id,
                            "supplier_id": item.supplier_id,
                            "quantity": item.quantity,
                            "quality_rank": item.quality_rank,
                            "price_rank": item.price_rank,
                            "candidate_count": item.candidate_count,
                            "quality_score": item.quality_score,
                            "best_quality_supplier": item.best_quality_supplier,
                            "best_quality_score": item.best_quality_score,
                            "is_quality_best": item.is_quality_best,
                            "is_price_best": item.is_price_best,
                        }
                    )
        finally:
            conn.close()

        summary = summarize_rank_records(first_records)
        run_rows.append(
            {
                "model": run.model,
                "framework": run.framework,
                "run_id": run.run_id,
                "is_survival_best": run.run_id in best_run_ids,
                "first_order_skus": summary["records"],
                "first_order_units": summary["total_units"],
                "first_order_quality_rank_mean": summary["quality_rank_mean"],
                "first_order_price_rank_mean": summary["price_rank_mean"],
                "first_order_quality_first_rate": summary["quality_first_rate"],
                "first_order_price_first_rate": summary["price_first_rate"],
                "first_order_unit_weighted_quality_rank_mean": summary[
                    "unit_weighted_quality_rank_mean"
                ],
                "first_order_unit_weighted_quality_first_rate": summary[
                    "unit_weighted_quality_first_rate"
                ],
            }
        )

    all_records = [
        RankRecord(
            sku_id=str(row["sku_id"]),
            supplier_id=str(row["supplier_id"]),
            current_date=str(row["first_order_date"]),
            quantity=int(row["quantity"] or 0),
            quality_rank=int(row["quality_rank"]) if row["quality_rank"] else None,
            price_rank=int(row["price_rank"]) if row["price_rank"] else None,
            candidate_count=int(row["candidate_count"] or 0),
            quality_score=finite(row["quality_score"]),
            best_quality_supplier=row["best_quality_supplier"],
            best_quality_score=finite(row["best_quality_score"]),
            is_quality_best=bool(row["is_quality_best"]) if row["is_quality_best"] is not None else None,
            is_price_best=bool(row["is_price_best"]) if row["is_price_best"] is not None else None,
        )
        for row in line_rows
    ]
    best_records = [
        item for item, row in zip(all_records, line_rows) if row["is_survival_best"]
    ]
    aggregate = {
        "all_llm_first_orders": summarize_rank_records(all_records),
        "survival_best_first_orders": summarize_rank_records(best_records),
    }
    return run_rows, line_rows, aggregate


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(item) for item in row) + " |")
    return lines


def render_report(payload: dict[str, Any]) -> str:
    history = payload["history_summary"]
    first_all = payload["first_order_summary"]["all_llm_first_orders"]
    first_best = payload["first_order_summary"]["survival_best_first_orders"]
    run_rows = sorted(
        [row for row in payload["first_order_by_run"] if row["is_survival_best"]],
        key=lambda row: row["model"],
    )
    lines = [
        "# Supplier Rank: History SQL vs First Model Orders",
        "",
        "## 结论先行",
        "",
        (
            "这个诊断使用内部 raw supplier quality 作为 post-hoc oracle，回答两个问题："
            "SQL 历史数据本身来自哪些 quality-rank supplier，以及 LLM runs 对每个 SKU 第一次下单时选到的 supplier rank。"
        ),
        "",
        (
            f"- SQL 历史数据中，unit-weighted mean quality rank 为 "
            f"{fmt(history['unit_weighted_quality_rank_mean'])}，"
            f"unit-weighted QualityFirst 为 {pct(history['unit_weighted_quality_first_rate'])}。"
        ),
        (
            f"- 全部 LLM first-order-by-SKU 中，mean quality rank 为 "
            f"{fmt(first_all['quality_rank_mean'])}，QualityFirst 为 {pct(first_all['quality_first_rate'])}，"
            f"PriceFirst 为 {pct(first_all['price_first_rate'])}。"
        ),
        (
            f"- Survival-best LLM first-order-by-SKU 中，mean quality rank 为 "
            f"{fmt(first_best['quality_rank_mean'])}，QualityFirst 为 {pct(first_best['quality_first_rate'])}，"
            f"PriceFirst 为 {pct(first_best['price_first_rate'])}。"
        ),
        "",
        "读法：如果模型第一次为某个 SKU 补货时已经识别到了 supplier quality structure，"
        "`first_order_quality_rank_mean` 应接近 1，`first_order_quality_first_rate` 应明显高于 price-first baseline。",
        "",
        "## 历史 SQL supplier rank",
        "",
        *markdown_table(
            ["Unit", "Records", "Units", "Distinct SKUs", "QRank Mean", "QFirst", "PriceFirst"],
            [
                [
                    "history order lines",
                    history["records"],
                    history["total_units"],
                    history["distinct_skus"],
                    fmt(history["quality_rank_mean"]),
                    pct(history["quality_first_rate"]),
                    pct(history["price_first_rate"]),
                ],
                [
                    "history unit-weighted",
                    history["records"],
                    history["total_units"],
                    history["distinct_skus"],
                    fmt(history["unit_weighted_quality_rank_mean"]),
                    pct(history["unit_weighted_quality_first_rate"]),
                    pct(history["unit_weighted_price_first_rate"]),
                ],
            ],
        ),
        "",
        "这里的 SQL 历史数据来自 `product_lifecycle` 中 `order_id LIKE 'hist-%'` 的 merchandise rows。"
        "line-level 指每个 `(order_id, sku_id, supplier_id, order_date)` 作为一条历史订货 line；"
        "unit-weighted 则按该 line 产生的 merchandise 数量加权。",
        "",
        "## 每个 survival-best run 的 first-order rank",
        "",
        *markdown_table(
            [
                "Model",
                "Framework",
                "First SKUs",
                "QRank",
                "PriceRank",
                "QFirst",
                "PriceFirst",
            ],
            [
                [
                    row["model"],
                    row["framework"],
                    row["first_order_skus"],
                    fmt(row["first_order_quality_rank_mean"]),
                    fmt(row["first_order_price_rank_mean"]),
                    pct(row["first_order_quality_first_rate"]),
                    pct(row["first_order_price_first_rate"]),
                ]
                for row in run_rows
            ],
        ),
        "",
        "## 机制解释",
        "",
        "这组 first-order 统计比全量 order-line 统计更接近“模型第一次面对某个 SKU supplier choice 时是否做对”。"
        "如果 first-order rank 已经很差，说明问题不是后期 drift 才出现，而是在初次 procurement decision 时就没有建立 supplier candidate comparison。",
        "",
        "这个结果直接支持 M2 的设计：在 `place_order` 前构建 candidate table，并把 raw-quality-best 判断作为 post-hoc diagnostic 或 gate oracle。"
        "如果实际机制允许用内部信息判断最佳 supplier，就可以直接用 `quality_rank=1` 作为 action revision 的触发条件；"
        "如果要保持 fair agent setting，则只能把它用于分析，不作为 agent-visible evidence。",
        "",
        "## 输出文件",
        "",
        f"- `{HISTORY_SKU_CSV.name}`：每个 SKU 在历史 SQL 中的 supplier rank 汇总。",
        f"- `{FIRST_ORDER_RUN_CSV.name}`：每个 run 的 first-order-by-SKU supplier rank 汇总。",
        f"- `{FIRST_ORDER_LINES_CSV.name}`：每个 run 每个 SKU 第一次下单的明细。",
        f"- `{SUMMARY_JSON.name}`：完整 machine-readable payload。",
        "",
        "## 证据边界",
        "",
        "本报告使用 hidden/raw quality 做 post-hoc oracle。它适合解释 benchmark 机制与 agent failure，"
        "但如果论文讨论的是模型当时可见信息下的公平决策，则必须明确 raw quality rank 不是 agent-visible signal。",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    history_summary, history_sku_rows = analyze_history_sql()
    first_order_by_run, first_order_lines, first_order_summary = analyze_first_orders()
    payload = {
        "history_summary": history_summary,
        "history_by_sku": history_sku_rows,
        "first_order_summary": first_order_summary,
        "first_order_by_run": first_order_by_run,
        "first_order_lines": first_order_lines,
    }
    SUMMARY_JSON.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    write_csv(HISTORY_SKU_CSV, history_sku_rows)
    write_csv(FIRST_ORDER_RUN_CSV, first_order_by_run)
    write_csv(FIRST_ORDER_LINES_CSV, first_order_lines)
    REPORT_MD.write_text(render_report(payload), encoding="utf-8")
    print(f"Wrote {SUMMARY_JSON}")
    print(f"Wrote {REPORT_MD}")
    print(f"Wrote {HISTORY_SKU_CSV}")
    print(f"Wrote {FIRST_ORDER_RUN_CSV}")
    print(f"Wrote {FIRST_ORDER_LINES_CSV}")


if __name__ == "__main__":
    main()
