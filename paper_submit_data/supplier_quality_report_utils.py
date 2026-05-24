"""Markdown rendering helpers for supplier quality failure analysis."""
from __future__ import annotations

import math
from typing import Any


def format_rate(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "N/A"
    return f"{value * 100:.1f}%"


def format_num(value: float | None) -> str:
    if value is None or not math.isfinite(float(value)):
        return "N/A"
    return f"{value:.2f}"


def markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("|" + "|".join("---" for _ in headers) + "|")
    for row in rows:
        lines.append("| " + " | ".join(str(item) for item in row) + " |")
    return lines


def render_report(payload: dict[str, Any]) -> str:
    all_summary = payload["summary"]["all_llm"]
    best_summary = payload["summary"]["survival_best_llm"]
    by_run = sorted(
        [row for row in payload["by_run"] if row["is_survival_best"]],
        key=lambda row: row["model"],
    )

    lines = [
        "# Supplier Quality Failure Analysis",
        "",
        "## 结论先行",
        "",
        (
            "大部分 LLM run 没有选中 raw `quality_score` 最高的 supplier，不是单一原因。"
            "主要问题是 supplier quality 在普通工具展示层没有显式暴露，而模型实际日志又显示出"
            "明显的 price-first 行为；因此这是“信息呈现不足 + 模型/提示策略偏向低价”的叠加问题。"
        ),
        "",
        "关键证据：",
        (
            f"- 全部 LLM runs 的加权 Quality First Top-1 rate 是 {format_rate(all_summary['quality_first_rate'])}，"
            f"Price First Top-1 rate 是 {format_rate(all_summary['price_first_rate'])}。"
        ),
        (
            f"- Survival-best runs 的 Quality First Top-1 rate 是 {format_rate(best_summary['quality_first_rate'])}，"
            f"Price First Top-1 rate 是 {format_rate(best_summary['price_first_rate'])}。"
        ),
        (
            f"- 在每个 survival-best run 最多 {payload['max_sample_lines_per_run']} 条成功下单 line 的日志审计样本中，"
            f"未选中最高质量 supplier 的 order lines 里，"
            f"{format_rate(best_summary['non_quality_first_price_first_rate'])} 仍然选择了最低价 supplier。"
        ),
        (
            f"- 同一日志审计样本里，下单前 supplier price query 覆盖率为 {format_rate(best_summary['supplier_price_query_rate'])}，"
            f"但 supplier return/rating 等 quality proxy query 覆盖率只有 "
            f"{format_rate(best_summary['quality_proxy_query_rate'])}。"
        ),
        "",
        "## 数据层面：quality 信息是否被提供？",
        "",
        "分三层看：",
        "",
        "1. 普通工具展示层没有直接提供 raw quality。`view_current_date_supplier_prices` 的 formatted 表格只有 `Supplier | SKU | Price`，没有 `quality_score`、return rate 或 quality rank。",
        "2. 原始 payload 层有部分隐藏信息。该工具内部 payload 保存 supplier entries；在 `execute_code` 中，tool proxy 会返回 `result` 字段，因此模型如果主动写代码检查 raw entry，有机会看到更完整结构。",
        "3. 可观察 proxy 层存在。环境提供 `view_supplier_returns_avg_rate` 和 `view_sku_avg_ratings`，可以用来估计 supplier/SKU quality，但日志显示模型下单前很少系统调用这些 proxy。",
        "",
        "所以不能说数据完全没有提供；更准确的说法是：benchmark 没有把 supplier quality 作为直接、可排名、可解释的 action precondition 呈现给 agent，模型需要主动发现并组合 proxy。",
        "",
        "## 行为层面：模型是否偏向低价？",
        "",
        f"下面这张表中 rank-rate 来自完整 `four_stage_metrics.csv`；`QualityProxyBeforeOrder` 来自 log-context audit sample，每个 survival-best run 最多取 {payload['max_sample_lines_per_run']} 条成功下单 line。",
        "",
        *markdown_table(
            ["Model", "Framework", "Order lines", "QualityFirst", "PriceFirst", "Avg QRank", "Avg PriceRank", "QualityProxyBeforeOrder"],
            [
                [
                    row["model"],
                    row["framework"],
                    row["order_lines"],
                    format_rate(row["quality_first_rate"]),
                    format_rate(row["price_first_rate"]),
                    format_num(row["avg_quality_rank"]),
                    format_num(row["avg_price_rank"]),
                    format_rate(row["quality_proxy_query_rate"]),
                ]
                for row in by_run
            ],
        ),
        "",
        "读法：如果模型真的在 quality-first 地选择 supplier，`QualityFirst` 应接近 100%，`Avg QRank` 应接近 1。实际多数 survival-best runs 的 `Avg QRank` 在 2.5 到 3.9 之间，同时 `PriceFirst` 往往显著高于 `QualityFirst`。",
        "",
        "## 具体日志案例",
        "",
    ]

    case_rows = []
    for row in payload["example_failures"]:
        ctx = row["pre_action_context"]
        best = row["candidates"][0] if row["candidates"] else {}
        selected = next(
            (item for item in row["candidates"] if item["supplier_id"] == row["selected_supplier"]),
            {},
        )
        case_rows.append(
            [
                row["model"], row["framework"], row["current_date"], row["sku_id"],
                row["selected_supplier"], row["selected_quality_rank"],
                row["selected_price_rank"], best.get("supplier_id"),
                selected.get("quality_score"), best.get("quality_score"),
                "Y" if ctx["had_supplier_price_query"] else "N",
                "Y" if ctx["had_quality_proxy_query"] else "N",
            ]
        )
    lines.extend(
        markdown_table(
            ["Model", "Framework", "Date", "SKU", "Chosen", "QRank", "PriceRank", "BestQ", "ChosenQ", "BestQScore", "PriceSeen", "QualityProxySeen"],
            case_rows,
        )
    )
    lines.extend(
        [
            "",
            "这些案例的共同模式是：模型经常已经查过 supplier price，但没有查或没有利用 supplier-level quality proxy；最终选择在 price rank 上更靠前，而不是 raw quality rank 上靠前的 supplier。",
            "",
            "## 机制解释",
            "",
            "1. **工具接口造成 price salience**：普通 supplier quote 表只显示价格，模型自然把“supplier selection”解释成 cheapest supplier selection。",
            "2. **prompt 示例强化了 cheapest heuristic**：`run_plan_and_act.py` 和 `run_step_reflection.py` 的 execute_code 示例显式用 `q['price'] < best['price']` 选择 supplier。这会把 agent 的默认策略锚定在低价，而不是 quality-adjusted cost。",
            "3. **quality proxy 需要跨工具组合**：真正合理的 quality-first 策略需要把 supplier quote、supplier return rate、SKU reviews/ratings、历史销量和库存风险拼起来。多数 run 没有稳定完成这一步。",
            "4. **action conversion 弱**：即使模型知道要补货，也常把“订哪个 supplier”降解为局部价格最小化，没有把 delayed returns / expiration / customer satisfaction 纳入 supplier objective。",
            "",
            "## 对论文的写法建议",
            "",
            "可以把这个 failure 写成一个 grounded diagnostic：LLM agent 不是完全看不到质量线索，而是没有形成稳定的信息获取和 action conversion loop。在 RetailBench 里，supplier quality 是 delayed, partially observable, and proxy-mediated；LLM 在 open-ended tool use 下倾向选择最显眼、最局部、最容易比较的 price signal。",
            "",
            "可行改进策略：",
            "- 工具改进：把 supplier quote 表扩展为 `price + return-rate proxy + rating proxy + delivery window + quality rank`，或新增 `rank_supplier_candidates(sku_id, objective)`。",
            "- Prompt 改进：删除 cheapest 示例，要求每次 `place_order` 前输出 candidate table，并显式说明选择 supplier 的 trade-off。",
            "- Policy 改进：引入 action validator，在下单前检查是否查询了 supplier return/rating proxy，并在未查询时触发补证据。",
            "- Memory 改进：把已观察到的 supplier failure/return signal 写入 notes，避免每天重新局部决策。",
            "",
            "## 证据边界",
            "",
            "这里的 `QualityFirst` 使用 hidden/raw `quality_score` 作为诊断 oracle，不等价于模型当时完全可见的信息。因此它不应被写成 fairness-normalized action correctness；更适合用作“agent 是否接近环境真实质量结构”的 diagnostic。对模型可见信息下的合理性，仍应结合 supplier return/rating proxy coverage 一起解释。",
            "",
        ]
    )
    return "\n".join(lines)
