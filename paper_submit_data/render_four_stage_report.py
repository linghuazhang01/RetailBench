#!/usr/bin/env python3
"""Render a stage-organized four-stage report with figures."""
from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import pandas as pd


SCRIPT_DIR = Path(__file__).resolve().parent
OUTPUT_DIR = SCRIPT_DIR / "outputs"
FIGURE_DIR = OUTPUT_DIR / "figures" / "four_stage"
SUPPLIER_QUALITY_CASES = OUTPUT_DIR / "supplier_quality_failure_cases.json"
SUPPLIER_FIRST_ORDER_RANKS = OUTPUT_DIR / "supplier_rank_history_first_order_analysis.json"

RUN_TYPE_STYLES = {
    "llm": {"label": "LLM model", "color": "#4C78A8", "marker": "o", "alpha": 0.78},
    "non_llm": {"label": "Hand-crafted policy", "color": "#111111", "marker": "*", "alpha": 0.88},
}


@dataclass(frozen=True)
class StageSpec:
    number: int
    title: str
    x_key: str
    y_key: str
    x_label: str
    y_label: str
    logic: list[str]
    interpretation: str
    table_columns: list[tuple[str, str]]


STAGES = [
    StageSpec(
        number=1,
        title="SKU Candidate Selection",
        x_key="s1_avg_acted_skus_per_day",
        y_key="s1_missed_high_demand_rate",
        x_label="Acted SKUs per day",
        y_label="Missed high-demand rate",
        logic=[
            "这一阶段问的是：模型每天是否先选对应该关注的 SKU。如果重要 SKU 没进入候选集合，后面的 query、订货、调价都不会发生。",
            "`Acted SKUs/day` 衡量管理覆盖面；`Missed high-demand rate` 用每日 sales top-10 加 stockout SKU 作为事后 high-demand signal，检查过去 3 天至当天是否被 action 覆盖。",
            "`Action->Sales7d` 检查被 action 的 SKU 在 7 天内是否真的产生销售；`Top10 share` 和 HHI 衡量动作是否过度集中在少数 SKU。",
        ],
        interpretation="理想点位在右上角：覆盖 SKU 多，同时 high-demand coverage 高。",
        table_columns=[
            ("Model", "model"), ("Framework", "framework"), ("Days", "run_days"),
            ("Acted/day", "s1_avg_acted_skus_per_day"),
            ("MissHD", "s1_missed_high_demand_rate"),
            ("Action->Sales7d", "s1_action_to_sales_overlap_7d"),
            ("Top10 share", "s1_top10_action_share"),
        ],
    ),
    StageSpec(
        number=2,
        title="Evidence Acquisition",
        x_key="s2_query_depth",
        y_key="s2_missing_critical_evidence_rate",
        x_label="Query depth",
        y_label="Missing critical evidence rate",
        logic=[
            "这一阶段问的是：模型在 action 前是否查到了足够、相关、同 SKU 的证据。",
            "对每个已执行 action，只看同一天 action 之前的 query，并把 query 映射到 inventory、sales history、supplier prices、supplier return/rating、current price、cost 等 required evidence categories。",
            "`QDepth` 是 required categories 命中比例；`Evidence match` 要求 query 覆盖同 SKU 或全局上下文；`Missing critical` 表示至少一个 required evidence category 缺失。",
        ],
        interpretation="理想点位在右上角：query depth 高，同时 evidence completeness 高。",
        table_columns=[
            ("Model", "model"), ("Framework", "framework"), ("Days", "run_days"),
            ("QDepth", "s2_query_depth"), ("Order QDepth", "s2_place_order_query_depth"),
            ("Price QDepth", "s2_modify_price_query_depth"),
            ("Missing critical", "s2_missing_critical_evidence_rate"),
        ],
    ),
    StageSpec(
        number=3,
        title="Action Conversion",
        x_key="s3_modify_price_distance_pct_mean",
        y_key="s3_supplier_quality_rank_mean",
        x_label="Mean price distance to optimal (%)",
        y_label="Mean supplier quality rank (1=best)",
        logic=[
            "这一阶段问的是：模型能否把证据转成正确 supplier、quantity 和 price，而不是只会查信息。",
            "价格分析：对 `modify_sku_price`，用历史销量与当前可见成本估计 optimal price，报告模型 new price 到 optimal price 的 mean/median/p90 percent distance，并拆分高于/低于 optimal 的比例。",
            "Supplier rank 分析：对每个 `place_order`，在同一 SKU/date 的候选 supplier 中计算 selected supplier 的 price rank 和 raw quality rank；rank=1 分别表示 cheapest supplier 或 highest-quality supplier。",
            "`PriceFirst%` 是 selected supplier 为 cheapest supplier 的比例；`QualityFirst%` 是 selected supplier 为 raw quality rank-1 的比例。二者一起判断模型是在按低价选 supplier，还是按质量选 supplier。",
        ],
        interpretation="理想点位在右上角：price closeness 高，同时 supplier quality score 高。",
        table_columns=[
            ("Model", "model"), ("Framework", "framework"), ("Days", "run_days"),
            ("ActionCorr", "s3_action_correction"),
            ("PriceDistMean%", "s3_modify_price_distance_pct_mean"),
            ("PriceDistP90%", "s3_modify_price_distance_pct_p90"),
            ("AboveOpt%", "s3_modify_price_above_optimal_rate"),
            ("BelowOpt%", "s3_modify_price_below_optimal_rate"),
            ("QRank", "s3_supplier_quality_rank_mean"),
            ("PriceRank", "s3_supplier_price_rank_mean"),
            ("PriceFirst%", "s3_supplier_price_first_rate"),
            ("QualityFirst%", "s3_supplier_quality_first_rate"),
        ],
    ),
    StageSpec(
        number=4,
        title="Temporal Follow-Up",
        x_key="s4_followup_action_rate_7d",
        y_key="s4_unresolved_event_rate_7d",
        x_label="Follow-up action rate within 7d",
        y_label="Unresolved event rate within 7d",
        logic=[
            "这一阶段问的是：模型是否持续跟踪前几天的动作，以及 stockout、return、expiration 这类 delayed signals。",
            "`Follow-up action rate` 检查 action SKU 在未来 7 天内是否再次被 action；`Follow q/a` 放宽为 query 或 action；`Unresolved` 检查 stockout/return/expiration SKU 在 7 天内是否完全没有后续 attention。",
            "`Continuity` 使用相邻 action SKU set 的 Jaccard；`Repeat no-attn` 衡量同一 SKU 重复出问题时，中间是否没有任何 query/action。",
        ],
        interpretation="理想点位在右上角：后续 action 跟踪率高，同时 resolved delayed-event rate 高。",
        table_columns=[
            ("Model", "model"), ("Framework", "framework"), ("Days", "run_days"),
            ("Follow action", "s4_followup_action_rate_7d"),
            ("Follow q/a", "s4_followup_query_or_action_rate_7d"),
            ("Unresolved", "s4_unresolved_event_rate_7d"),
            ("Continuity", "s4_focus_continuity_jaccard"),
            ("Repeat no-attn", "s4_repeated_error_without_intervention_rate"),
        ],
    ),
]


def safe_float(value: Any) -> float | None:
    if pd.isna(value) or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def fmt(value: Any, digits: int = 4) -> str:
    number = safe_float(value)
    if number is None:
        return "--"
    if abs(number - round(number)) < 1e-9:
        return str(int(round(number)))
    if abs(number) >= 1000:
        return f"{number:,.2f}"
    return f"{number:.{digits}f}"


def short_label(row: pd.Series) -> str:
    model = str(row["model"])
    aliases = {
        "DeepSeek-V4-Pro": "DeepSeek",
        "Qwen3.5-397B-A17B": "Qwen",
        "MiniMax-M2.5": "MiniMax",
        "Non-LLM Heuristic": "Policy",
    }
    name = aliases.get(model, model.replace("-4.3", "").replace("-K2.6", ""))
    return name


def select_survival_first(rows: pd.DataFrame) -> pd.DataFrame:
    sort_cols = ["model", "run_days", "final_networth", "total_sales"]
    selected = (
        rows.sort_values(sort_cols, ascending=[True, False, False, False])
        .groupby("model", as_index=False)
        .head(1)
        .copy()
    )
    return selected.sort_values(["run_type", "model"], ascending=[True, True])


def display_rows_for_stage(rows: pd.DataFrame, stage: StageSpec) -> pd.DataFrame:
    """Return stage-specific display metrics without mutating the raw CSV data."""
    display = rows.copy()
    if stage.number == 2:
        policy_mask = display["run_type"].astype(str) != "llm"
        display.loc[policy_mask, "s2_query_depth"] = 1.0
        display.loc[policy_mask, "s2_place_order_query_depth"] = 1.0
        display.loc[policy_mask, "s2_modify_price_query_depth"] = 1.0
        display.loc[policy_mask, "s2_missing_critical_evidence_rate"] = 0.0
    return display


def markdown_table(rows: pd.DataFrame, columns: list[tuple[str, str]]) -> list[str]:
    lines = [
        "| " + " | ".join(title for title, _ in columns) + " |",
        "| " + " | ".join("---" for _ in columns) + " |",
    ]
    for _, row in rows.iterrows():
        values = []
        for _, key in columns:
            raw = row.get(key)
            values.append(str(raw) if key in {"model", "framework"} else fmt(raw))
        lines.append("| " + " | ".join(values) + " |")
    return lines


def plot_stage(rows: pd.DataFrame, selected: pd.DataFrame, stage: StageSpec, figure_dir: Path) -> tuple[Path, Path]:
    all_path = figure_dir / f"stage{stage.number}_all_runs.png"
    selected_path = figure_dir / f"stage{stage.number}_survival_best_runs.png"
    _plot_scatter(rows, stage, all_path, f"Stage {stage.number}: All Runs")
    _plot_scatter(selected, stage, selected_path, f"Stage {stage.number}: Survival-First Best Runs")
    return all_path, selected_path


def _plot_coordinates(rows: pd.DataFrame, stage: StageSpec) -> tuple[pd.Series, pd.Series, str, str]:
    """Return visualization coordinates where larger x/y is always better."""
    if stage.number == 1:
        x = pd.to_numeric(rows["s1_avg_acted_skus_per_day"], errors="coerce")
        y = 1.0 - pd.to_numeric(rows["s1_missed_high_demand_rate"], errors="coerce")
        return x, y.clip(lower=0.0, upper=1.0), "Acted SKUs per day", "High-demand coverage rate"
    if stage.number == 2:
        x = pd.to_numeric(rows["s2_query_depth"], errors="coerce")
        y = 1.0 - pd.to_numeric(rows["s2_missing_critical_evidence_rate"], errors="coerce")
        return x, y.clip(lower=0.0, upper=1.0), "Query depth", "Evidence completeness rate"
    if stage.number == 3:
        distance = pd.to_numeric(rows["s3_modify_price_distance_pct_mean"], errors="coerce")
        rank = pd.to_numeric(rows["s3_supplier_quality_rank_mean"], errors="coerce")
        candidates = pd.to_numeric(rows["s3_supplier_candidate_count_mean"], errors="coerce").fillna(5.0)
        x = 1.0 / (1.0 + distance.clip(lower=0.0) / 100.0)
        y = (candidates - rank) / (candidates - 1.0)
        return x.clip(lower=0.0, upper=1.0), y.clip(lower=0.0, upper=1.0), "Price closeness score", "Supplier quality score"
    if stage.number == 4:
        x = pd.to_numeric(rows["s4_followup_action_rate_7d"], errors="coerce")
        y = 1.0 - pd.to_numeric(rows["s4_unresolved_event_rate_7d"], errors="coerce")
        return x, y.clip(lower=0.0, upper=1.0), "Follow-up action rate within 7d", "Resolved event rate within 7d"
    x = pd.to_numeric(rows[stage.x_key], errors="coerce")
    y = pd.to_numeric(rows[stage.y_key], errors="coerce")
    return x, y, stage.x_label, stage.y_label


def _plot_scatter(rows: pd.DataFrame, stage: StageSpec, path: Path, title: str) -> None:
    plot_rows = rows.copy()
    plot_rows["_plot_x"], plot_rows["_plot_y"], x_label, y_label = _plot_coordinates(plot_rows, stage)
    plot_rows["run_days"] = pd.to_numeric(plot_rows["run_days"], errors="coerce")
    plot_rows = plot_rows.dropna(subset=["_plot_x", "_plot_y"])

    fig, ax = plt.subplots(figsize=(8.8, 5.4), dpi=180)
    for run_type, group in plot_rows.groupby("run_type"):
        style = RUN_TYPE_STYLES.get(
            str(run_type),
            {"label": str(run_type), "color": "#777777", "marker": "o", "alpha": 0.78},
        )
        sizes = 50 + group["run_days"].fillna(0) * 1.8
        ax.scatter(
            group["_plot_x"],
            group["_plot_y"],
            s=sizes,
            alpha=style["alpha"],
            color=style["color"],
            marker=style["marker"],
            edgecolor="white",
            linewidth=0.8,
            label=style["label"],
        )
        for _, row in group.iterrows():
            ax.annotate(
                short_label(row),
                (row["_plot_x"], row["_plot_y"]),
                xytext=(4, 3),
                textcoords="offset points",
                fontsize=6.8,
                alpha=0.85,
            )

    ax.set_title(title, fontsize=12, pad=10)
    ax.set_xlabel(x_label, fontsize=10)
    ax.set_ylabel(y_label, fontsize=10)
    ax.grid(True, alpha=0.22, linewidth=0.7)
    ax.legend(loc="best", fontsize=7, frameon=True)
    ax.text(
        0.99,
        0.02,
        "Bubble size = survival days",
        transform=ax.transAxes,
        ha="right",
        va="bottom",
        fontsize=7,
        color="#555555",
    )
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def summarize_stage(rows: pd.DataFrame, selected: pd.DataFrame, stage: StageSpec) -> list[str]:
    full = rows.copy()
    sel = selected.copy()
    for key in [stage.x_key, stage.y_key, "run_days", "final_networth"]:
        full[key] = pd.to_numeric(full[key], errors="coerce")
        sel[key] = pd.to_numeric(sel[key], errors="coerce")

    full_valid = full.dropna(subset=[stage.x_key, stage.y_key])
    sel_valid = sel.dropna(subset=[stage.x_key, stage.y_key])
    lines = [
        f"- 全量 runs：`{stage.x_label}` 范围为 {metric_range(full_valid, stage.x_key)}；`{stage.y_label}` 范围为 {metric_range(full_valid, stage.y_key)}。",
    ]
    if not full_valid.empty:
        if stage.number == 3:
            closest_price = full_valid.loc[full_valid[stage.x_key].idxmin()]
            best_quality_rank = full_valid.loc[full_valid[stage.y_key].idxmin()]
            worst_price = full_valid.loc[full_valid[stage.x_key].idxmax()]
            lines.append(f"- 全量数据中价格最接近 optimal 的是 {short_label(closest_price)}，mean distance 为 {fmt(closest_price[stage.x_key])}%；supplier quality rank 最好的是 {short_label(best_quality_rank)}，平均 rank 为 {fmt(best_quality_rank[stage.y_key])}；价格距离最大的是 {short_label(worst_price)}，mean distance 为 {fmt(worst_price[stage.x_key])}%。")
        else:
            best_x = full_valid.loc[full_valid[stage.x_key].idxmax()]
            worst_y = full_valid.loc[full_valid[stage.y_key].idxmax()]
            lines.append(f"- 全量数据中 `{stage.x_label}` 最高的是 {short_label(best_x)}，值为 {fmt(best_x[stage.x_key])}；`{stage.y_label}` 最高的是 {short_label(worst_y)}，值为 {fmt(worst_y[stage.y_key])}。")
    lines.append(f"- Survival-first best runs：`{stage.x_label}` 范围为 {metric_range(sel_valid, stage.x_key)}；`{stage.y_label}` 范围为 {metric_range(sel_valid, stage.y_key)}。")
    if not sel_valid.empty:
        best_selected = sel_valid.loc[sel_valid["run_days"].idxmax()]
        lines.append(f"- best-run 图用于回答：如果每个模型只保留 survival day 最多的 run，模型间差异是否仍然存在。该视角避免短命高 networth run 混淆长期经营能力。")
        lines.append(f"- 在 selected 视角下，代表性长生存 run 包括 {short_label(best_selected)}，survival days 为 {fmt(best_selected['run_days'])}。")
    return lines


def metric_range(rows: pd.DataFrame, key: str) -> str:
    values = pd.to_numeric(rows[key], errors="coerce").dropna()
    if values.empty:
        return "--"
    return f"{fmt(values.min())} - {fmt(values.max())}, mean {fmt(values.mean())}"


def stage_specific_notes(rows: pd.DataFrame, selected: pd.DataFrame, stage: StageSpec) -> list[str]:
    if stage.number != 3:
        return []
    cols = [
        "s3_modify_price_distance_pct_mean",
        "s3_supplier_quality_rank_mean",
        "s3_supplier_price_rank_mean",
        "s3_supplier_price_first_rate",
        "s3_supplier_quality_first_rate",
    ]
    view = selected.copy()
    for col in cols:
        view[col] = pd.to_numeric(view[col], errors="coerce")
    valid = view.dropna(subset=cols)
    if valid.empty:
        return []
    price_best = valid.loc[valid["s3_modify_price_distance_pct_mean"].idxmin()]
    quality_best = valid.loc[valid["s3_supplier_quality_rank_mean"].idxmin()]
    price_first = valid.loc[valid["s3_supplier_price_first_rate"].idxmax()]
    quality_first = valid.loc[valid["s3_supplier_quality_first_rate"].idxmax()]
    lines = [
        "### Stage 3 专项解读：price optimality 与 supplier preference",
        "",
        f"- 价格距离：selected best runs 中，mean price distance 最小的是 {short_label(price_best)}，为 {fmt(price_best['s3_modify_price_distance_pct_mean'])}%；这表示其调价最接近基于历史销量与成本估计的 optimal price。",
        f"- Supplier 平均 rank：quality rank 最好的是 {short_label(quality_best)}，平均 raw quality rank 为 {fmt(quality_best['s3_supplier_quality_rank_mean'])}；rank 越接近 1，越常选到高质量 supplier。",
        f"- Price-first 倾向：`PriceFirst%` 最高的是 {short_label(price_first)}，为 {fmt(price_first['s3_supplier_price_first_rate'])}；这表示它最常选择 cheapest supplier。",
        f"- Quality-first 倾向：`QualityFirst%` 最高的是 {short_label(quality_first)}，为 {fmt(quality_first['s3_supplier_quality_first_rate'])}；这表示它最常选择 raw quality 最优 supplier。",
        "- 如果 `PriceFirst%` 高但 `QualityFirst%` 低，说明模型更像是在按低价采购；如果 `QualityFirst%` 高且 return ratio 低，才更接近 RetailBench 需要的质量优先采购策略。",
        "",
    ]
    lines.extend(stage3_supplier_quality_diagnosis())
    return lines


def stage3_supplier_quality_diagnosis() -> list[str]:
    if not SUPPLIER_QUALITY_CASES.exists():
        return []
    try:
        payload = json.loads(SUPPLIER_QUALITY_CASES.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []

    all_summary = payload.get("summary", {}).get("all_llm", {})
    best_summary = payload.get("summary", {}).get("survival_best_llm", {})
    sample_n = payload.get("max_sample_lines_per_run", 30)
    q_all = all_summary.get("quality_first_rate")
    p_all = all_summary.get("price_first_rate")
    q_best = best_summary.get("quality_first_rate")
    p_best = best_summary.get("price_first_rate")
    price_query = best_summary.get("supplier_price_query_rate")
    quality_proxy = best_summary.get("quality_proxy_query_rate")
    non_quality_price = best_summary.get("non_quality_first_price_first_rate")
    first_order_line = ""
    if SUPPLIER_FIRST_ORDER_RANKS.exists():
        try:
            first_payload = json.loads(SUPPLIER_FIRST_ORDER_RANKS.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            first_payload = {}
        history = first_payload.get("history_summary", {})
        first_summary = first_payload.get("first_order_summary", {})
        first_all = first_summary.get("all_llm_first_orders", {})
        first_best = first_summary.get("survival_best_first_orders", {})
        first_order_line = (
            f"- First-order 诊断显示这个问题从首次采购就存在：SQL 历史数据的 unit-weighted raw quality rank 为 "
            f"{fmt(history.get('unit_weighted_quality_rank_mean'))}，QualityFirst 为 "
            f"{fmt_rate(history.get('unit_weighted_quality_first_rate'))}，PriceFirst 仅 "
            f"{fmt_rate(history.get('unit_weighted_price_first_rate'))}；全部 LLM runs 每个 SKU 第一次成功下单的 mean quality rank 为 "
            f"{fmt(first_all.get('quality_rank_mean'))}，QualityFirst 为 "
            f"{fmt_rate(first_all.get('quality_first_rate'))}，PriceFirst 为 "
            f"{fmt_rate(first_all.get('price_first_rate'))}；survival-best runs 的 first-order mean quality rank 为 "
            f"{fmt(first_best.get('quality_rank_mean'))}，QualityFirst 为 "
            f"{fmt_rate(first_best.get('quality_first_rate'))}，PriceFirst 为 "
            f"{fmt_rate(first_best.get('price_first_rate'))}。因此模型并没有在首次补货时继承或识别历史数据中的 high-quality supplier prior。"
        )

    lines = [
        "### Stage 3 机制诊断：为什么没有选到最高质量 supplier",
        "",
        f"- 全量 LLM runs 的 `QualityFirst%` 只有 {fmt_rate(q_all)}，但 `PriceFirst%` 为 {fmt_rate(p_all)}；survival-best runs 中 `QualityFirst%` 为 {fmt_rate(q_best)}，`PriceFirst%` 为 {fmt_rate(p_best)}。这说明 supplier choice 的主导错误不是随机噪声，而是系统性偏向低价 supplier。",
        f"- 日志审计样本（每个 survival-best run 最多 {sample_n} 条成功下单 line）显示，下单前 supplier price query 覆盖率为 {fmt_rate(price_query)}，但 supplier return/rating 等 quality proxy query 覆盖率只有 {fmt_rate(quality_proxy)}；在未选中 raw quality 最优 supplier 的 order lines 中，{fmt_rate(non_quality_price)} 仍然选择了最低价 supplier。",
    ]
    if first_order_line:
        lines.append(first_order_line)
    lines.extend([
        "- 因此 Stage 3 的失败应解释为“信息呈现不足 + action conversion 不足”的叠加：普通 supplier price 工具把 price 暴露得最直接，而 raw quality 或 quality proxy 需要模型主动读取/组合；即使部分 run 查询了 reviews/returns，也经常没有把这些 evidence 转成 supplier ranking。",
        "- 论文写作上，这个结果应被表述为 supplier quality 是 delayed, partially observable, and proxy-mediated；当前 LLM agent 倾向使用最显眼、最局部、最容易比较的 price signal，而不是建立 `supplier candidate table -> quality-adjusted ranking -> place_order` 的稳定闭环。",
        f"- Trace-level evidence 和具体 SKU/supplier cases 见 `supplier_quality_failure_analysis.md`、`supplier_quality_failure_cases.json` 与 `supplier_rank_history_first_order_analysis.md`。",
        "",
    ])
    return lines


def fmt_rate(value: Any) -> str:
    parsed = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    if pd.isna(parsed):
        return "N/A"
    return f"{float(parsed) * 100:.1f}%"


def render_report(rows: pd.DataFrame, output_dir: Path) -> str:
    figure_dir = output_dir / "figures" / "four_stage"
    figure_dir.mkdir(parents=True, exist_ok=True)
    selected = select_survival_first(rows)

    lines: list[str] = [
        "# RetailBench Four-Stage Run Analysis",
        "",
        "## Scope",
        "",
        f"本报告覆盖 `{len(rows)}` 个 runs：`{len(rows[rows['run_type'] == 'llm'])}` 个 LLM runs 和 `{len(rows[rows['run_type'] != 'llm'])}` 个 Non-LLM run。分析单位是 run；全量图展示每个 run，best-run 图只保留每个模型 survival day 最多的 run。",
        "",
        "Non-LLM Heuristic 是显式规则 policy；Stage 2 展示中将其 evidence depth 和 completeness 记为 1，因为规则已经完整指定了所需证据，而不是通过自然语言工具查询逐项获取。所有 retrospective metrics 只用于诊断，不声称 agent 当时可以看到未来销量。",
        "",
        "## Executive Summary",
        "",
        "- 四阶段分析把失败拆成：选 SKU、查证据、转动作、长期跟踪。任何一层失败都会放大到 survival、networth 和 sales 差距。",
        "- 当前数据最强的瓶颈是 Stage 3：很多 run 的 query depth 很高，但 action correction 仍低，说明问题不只是工具调用不足。",
        "- Non-LLM gap 的核心不是语言能力，而是它有显式、稳定、状态化的 SKU coverage、supplier selection 和 follow-up policy。",
        "",
    ]

    for stage in STAGES:
        stage_rows = display_rows_for_stage(rows, stage)
        stage_selected = select_survival_first(stage_rows)
        all_fig, selected_fig = plot_stage(stage_rows, stage_selected, stage, figure_dir)
        rel_all = all_fig.relative_to(output_dir)
        rel_selected = selected_fig.relative_to(output_dir)
        lines.extend([
            f"## Stage {stage.number}: {stage.title}",
            "",
            "### 数据分析逻辑",
            "",
            *[f"- {item}" for item in stage.logic],
            f"- 图的读法：{stage.interpretation}",
            "",
            "### 图表 1：全量 runs",
            "",
            f"![Stage {stage.number} all runs]({rel_all.as_posix()})",
            "",
            *summarize_stage(stage_rows, stage_selected, stage)[:2],
            "",
            *markdown_table(stage_rows, stage.table_columns),
            "",
            "### 图表 2：Survival-first best runs",
            "",
            f"![Stage {stage.number} survival best runs]({rel_selected.as_posix()})",
            "",
            *summarize_stage(stage_rows, stage_selected, stage)[2:],
            "",
            *markdown_table(stage_selected, stage.table_columns),
            "",
            *stage_specific_notes(stage_rows, stage_selected, stage),
        ])

    lines.extend([
        "## Interpretation Boundary",
        "",
        "这些图和表是 descriptive diagnostics：它们说明当前数据里哪些 operational stages 与 survival/networth 差异一起变化，但不构成因果证明。对于只有一个 run 的 GPT-5.5 和 Non-LLM Heuristic，报告只做个案对比；对于三 run 模型，best-run 视角使用 survival-first rule，而不是按 networth 事后挑选。",
    ])
    return "\n".join(lines) + "\n"


def write_figure_catalog(output_dir: Path) -> None:
    lines = [
        "# Four-Stage Figure Catalog",
        "",
        "| Figure | Purpose | Data scope |",
        "| --- | --- | --- |",
    ]
    for stage in STAGES:
        lines.append(f"| `figures/four_stage/stage{stage.number}_all_runs.png` | Stage {stage.number} all-run diagnostic scatter | 20 runs where x/y are defined |")
        lines.append(f"| `figures/four_stage/stage{stage.number}_survival_best_runs.png` | Stage {stage.number} survival-first selected diagnostic scatter | one run per model/baseline |")
    (output_dir / "four_stage_figure_catalog.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Render four-stage report with per-stage figures.")
    parser.add_argument("--metrics-csv", type=Path, default=OUTPUT_DIR / "four_stage_metrics.csv")
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = pd.read_csv(args.metrics_csv)
    report = render_report(rows, args.output_dir)
    (args.output_dir / "four_stage_analysis_report.md").write_text(report, encoding="utf-8")
    write_figure_catalog(args.output_dir)
    print(f"Wrote {args.output_dir / 'four_stage_analysis_report.md'}")
    print(f"Wrote figures to {args.output_dir / 'figures' / 'four_stage'}")


if __name__ == "__main__":
    main()
