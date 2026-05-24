from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
METRICS_CSV = ROOT / "paper_submit_data" / "outputs" / "four_stage_metrics.csv"
SUPPLIER_DIAG_JSON = ROOT / "paper_submit_data" / "outputs" / "supplier_quality_failure_cases.json"
FIGURE_DIR = ROOT / "paper" / "latex_arr_revision" / "figures"


MODEL_LABELS = {
    "DeepSeek-V4-Pro": "Deepseek",
    "GLM-5.1": "GLM",
    "GPT-5.5": "GPT",
    "Grok-4.3": "Grok",
    "Kimi-K2.6": "Kimi",
    "MiniMax-M2.5": "Minimax",
    "Qwen3.5-397B-A17B": "Qwen",
    "Non-LLM Heuristic": "Policy",
}


PLOTS = [
    {
        "filename": "stage1_sku_selection_single",
        "title": "Stage 1: Product Selection",
        "x": "s1_avg_acted_skus_per_day",
        "y": "s1_high_demand_coverage",
        "xlabel": "Acted products / day",
        "ylabel": "High-demand coverage",
        "include_policy": True,
    },
    {
        "filename": "stage2_evidence_single",
        "title": "Stage 2: Evidence",
        "x": "s2_query_depth",
        "y": "s2_evidence_completeness",
        "xlabel": "Query depth",
        "ylabel": "Evidence completeness",
        "include_policy": True,
    },
    {
        "filename": "stage3_action_single",
        "title": "Stage 3: Action Conversion",
        "x": "s3_price_closeness",
        "y": "s3_supplier_quality_score",
        "xlabel": "Price closeness",
        "ylabel": "Supplier quality score",
        "include_policy": True,
    },
    {
        "filename": "stage4_followup_single",
        "title": "Stage 4: Follow-Up",
        "x": "s4_followup_action_rate_7d",
        "y": "s4_resolved_event_rate",
        "xlabel": "Follow-up action rate",
        "ylabel": "Resolved event rate",
        "include_policy": True,
    },
]

LABEL_OFFSETS = {
    "stage1_sku_selection_single": {
        "GLM-5.1": (5, -9, "left", "top"),
        "Grok-4.3": (5, 6, "left", "bottom"),
        "Qwen3.5-397B-A17B": (5, -10, "left", "top"),
        "MiniMax-M2.5": (-4, 8, "right", "bottom"),
        "Non-LLM Heuristic": (0, 10, "center", "bottom"),
    },
    "stage2_evidence_single": {
        "Grok-4.3": (-12, -4, "right", "top"),
        "Qwen3.5-397B-A17B": (8, 10, "left", "bottom"),
        "MiniMax-M2.5": (8, -2, "left", "top"),
        "GLM-5.1": (8, 12, "left", "bottom"),
        "Kimi-K2.6": (8, -2, "left", "top"),
        "DeepSeek-V4-Pro": (-8, -10, "right", "top"),
        "GPT-5.5": (8, -2, "left", "center"),
        "Non-LLM Heuristic": (0, 9, "center", "bottom"),
    },
    "stage3_action_single": {
        "Grok-4.3": (10, 8, "left", "bottom"),
        "GLM-5.1": (8, 12, "left", "bottom"),
        "GPT-5.5": (8, 8, "left", "bottom"),
        "DeepSeek-V4-Pro": (10, 12, "left", "bottom"),
        "MiniMax-M2.5": (10, -2, "left", "center"),
        "Qwen3.5-397B-A17B": (10, -12, "left", "top"),
        "Kimi-K2.6": (10, -18, "left", "top"),
        "Non-LLM Heuristic": (0, 9, "center", "bottom"),
    },
    "stage4_followup_single": {
        "GLM-5.1": (8, 12, "left", "bottom"),
        "Grok-4.3": (-10, 8, "right", "bottom"),
        "MiniMax-M2.5": (-12, 15, "right", "bottom"),
        "Qwen3.5-397B-A17B": (9, -2, "left", "top"),
        "Kimi-K2.6": (-16, 12, "right", "bottom"),
        "GPT-5.5": (0, 16, "center", "bottom"),
        "DeepSeek-V4-Pro": (8, 14, "left", "bottom"),
        "Non-LLM Heuristic": (-36, 8, "right", "bottom"),
    },
}

LEGEND_LOCS = {
    "stage2_evidence_single": "upper left",
    "stage3_action_single": "upper left",
}


def select_survival_first(df: pd.DataFrame) -> pd.DataFrame:
    sort_cols = ["model", "run_days", "final_networth", "total_sales"]
    selected = (
        df.sort_values(sort_cols, ascending=[True, False, False, False])
        .groupby("model", as_index=False)
        .head(1)
        .copy()
    )
    return selected.sort_values("model").reset_index(drop=True)


def add_derived_metrics(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["s1_high_demand_coverage"] = 1.0 - out["s1_missed_high_demand_rate"]
    out["s2_evidence_completeness"] = 1.0 - out["s2_missing_critical_evidence_rate"]
    out["s4_resolved_event_rate"] = 1.0 - out["s4_unresolved_event_rate_7d"]

    policy_mask = out["run_type"] != "llm"
    out.loc[policy_mask, "s2_query_depth"] = 1.0
    out.loc[policy_mask, "s2_evidence_completeness"] = 1.0

    price_distance = out["s3_modify_price_distance_pct_mean"]
    max_distance = price_distance.dropna().max()
    out["s3_price_closeness"] = (1.0 - price_distance / max_distance).clip(0.0, 1.0)
    out["s3_supplier_quality_score"] = (
        (5.0 - out["s3_supplier_quality_rank_mean"]) / 4.0
    ).clip(0.0, 1.0)
    return out


def setup_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "serif",
            "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
            "font.size": 10.5,
            "axes.titlesize": 11.0,
            "axes.labelsize": 10.5,
            "xtick.labelsize": 9.5,
            "ytick.labelsize": 9.5,
            "legend.fontsize": 9.0,
            "axes.spines.top": False,
            "axes.spines.right": False,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
        }
    )


def draw_better_arrow(ax: plt.Axes) -> None:
    ax.annotate(
        "Better",
        xy=(0.95, 0.94),
        xytext=(0.72, 0.80),
        xycoords="axes fraction",
        textcoords="axes fraction",
        arrowprops={"arrowstyle": "->", "lw": 1.1, "color": "#287233"},
        color="#287233",
        fontsize=9.0,
        fontweight="bold",
        ha="center",
        va="center",
    )


def pad_limits(values: pd.Series, lower: float | None = None, upper: float | None = None) -> tuple[float, float]:
    finite = values.dropna()
    lo = float(finite.min()) if lower is None else lower
    hi = float(finite.max()) if upper is None else upper
    if hi == lo:
        delta = 0.05 if hi == 0 else abs(hi) * 0.05
    else:
        delta = (hi - lo) * 0.12
    return lo - delta, hi + delta


def plot_stage(df: pd.DataFrame, spec: dict[str, object]) -> None:
    plot_df = df.copy()
    if not spec["include_policy"]:
        plot_df = plot_df[plot_df["run_type"] == "llm"]
    plot_df = plot_df.dropna(subset=[spec["x"], spec["y"]])

    fig, ax = plt.subplots(figsize=(3.35, 2.5))
    llm = plot_df[plot_df["run_type"] == "llm"]
    policy = plot_df[plot_df["run_type"] != "llm"]

    ax.scatter(
        llm[spec["x"]],
        llm[spec["y"]],
        s=58,
        color="#2F6DB3",
        edgecolor="white",
        linewidth=0.7,
        label="LLM model",
        zorder=3,
    )
    if not policy.empty:
        ax.scatter(
            policy[spec["x"]],
            policy[spec["y"]],
            s=110,
            marker="*",
            color="#C98910",
            edgecolor="#5E3B00",
            linewidth=0.6,
            label="Hand-crafted policy",
            zorder=4,
        )

    for _, row in plot_df.iterrows():
        label = MODEL_LABELS.get(row["model"], row["model"])
        dx, dy, ha, va = LABEL_OFFSETS.get(spec["filename"], {}).get(
            row["model"],
            (4 if row["run_type"] == "llm" else -2, 4 if row["run_type"] == "llm" else 7, "left" if row["run_type"] == "llm" else "center", "bottom"),
        )
        ax.annotate(
            label,
            (row[spec["x"]], row[spec["y"]]),
            xytext=(dx, dy),
            textcoords="offset points",
            fontsize=8.6,
            ha=ha,
            va=va,
        )

    ax.set_xlabel(spec["xlabel"])
    ax.set_ylabel(spec["ylabel"])
    ax.grid(True, color="#D8DEE9", linewidth=0.5, alpha=0.7)
    draw_better_arrow(ax)

    x_values = plot_df[spec["x"]]
    y_values = plot_df[spec["y"]]
    if spec["x"] in {"s2_query_depth", "s3_price_closeness", "s4_followup_action_rate_7d"}:
        ax.set_xlim(0.0, 1.12 if not policy.empty else 1.15)
    else:
        ax.set_xlim(*pad_limits(x_values, lower=0.0))
    if spec["y"] in {
        "s1_high_demand_coverage",
        "s2_evidence_completeness",
        "s3_supplier_quality_score",
        "s4_resolved_event_rate",
    }:
        ax.set_ylim(0.0, 1.12 if spec["filename"] == "stage4_followup_single" or not policy.empty else 1.04)
    else:
        ax.set_ylim(*pad_limits(y_values))

    ax.legend(frameon=False, loc=LEGEND_LOCS.get(spec["filename"], "lower right"))
    fig.tight_layout(pad=0.4)
    for ext in ("pdf", "png"):
        path = FIGURE_DIR / f"{spec['filename']}.{ext}"
        fig.savefig(path, dpi=300, bbox_inches="tight", pad_inches=0.03)
        print(f"Wrote {path}")
    plt.close(fig)


def plot_supplier_selection_diagnostic(df: pd.DataFrame) -> None:
    with SUPPLIER_DIAG_JSON.open("r", encoding="utf-8") as handle:
        supplier_diag = json.load(handle)
    summary = supplier_diag["summary"]
    policy = df[df["run_type"] != "llm"].iloc[0]

    rows = [
        (
            "All LLM\norders",
            summary["all_llm"]["quality_first_rate"],
            summary["all_llm"]["price_first_rate"],
        ),
        (
            "Survival-best\nLLM orders",
            summary["survival_best_llm"]["quality_first_rate"],
            summary["survival_best_llm"]["price_first_rate"],
        ),
        (
            "Hand-crafted\npolicy",
            policy["s3_supplier_quality_first_rate"],
            policy["s3_supplier_price_first_rate"],
        ),
    ]

    labels = [row[0] for row in rows]
    quality = [row[1] * 100.0 for row in rows]
    price = [row[2] * 100.0 for row in rows]
    x = range(len(rows))
    width = 0.34

    fig, ax = plt.subplots(figsize=(5.8, 2.65))
    bars_q = ax.bar(
        [i - width / 2 for i in x],
        quality,
        width=width,
        label="QualityFirst",
        color="#2F6DB3",
    )
    bars_p = ax.bar(
        [i + width / 2 for i in x],
        price,
        width=width,
        label="PriceFirst",
        color="#C98910",
    )
    for bars in (bars_q, bars_p):
        for bar in bars:
            value = bar.get_height()
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                value + 1.5,
                f"{value:.1f}",
                ha="center",
                va="bottom",
                fontsize=8.8,
            )

    ax.set_ylabel("Order lines (%)")
    ax.set_ylim(0, 108)
    ax.set_xticks(list(x))
    ax.set_xticklabels(labels)
    ax.grid(True, axis="y", color="#D8DEE9", linewidth=0.5, alpha=0.7)
    ax.legend(frameon=False, loc="upper left", ncol=2)
    fig.tight_layout(pad=0.5)

    for ext in ("pdf", "png"):
        path = FIGURE_DIR / f"supplier_selection_diagnostic.{ext}"
        fig.savefig(path, dpi=300, bbox_inches="tight", pad_inches=0.03)
        print(f"Wrote {path}")
    plt.close(fig)


def main() -> None:
    setup_style()
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    df = add_derived_metrics(select_survival_first(pd.read_csv(METRICS_CSV)))
    for spec in PLOTS:
        plot_stage(df, spec)
    plot_supplier_selection_diagnostic(df)


if __name__ == "__main__":
    main()
