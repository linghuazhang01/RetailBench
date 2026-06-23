const DATA_URL = "assets/benchmark_results.json";

const formatCurrency = (value) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  }).format(value);

const formatNumber = (value, digits = 0) =>
  new Intl.NumberFormat("en-US", {
    maximumFractionDigits: digits,
  }).format(value);

const formatRatio = (value) => `${(value * 100).toFixed(1)}%`;

const formatCompactCurrency = (value) => {
  const sign = value < 0 ? "-" : "";
  const abs = Math.abs(value);
  if (abs >= 1000) return `${sign}$${Math.round(abs / 1000)}k`;
  return `${sign}$${Math.round(abs)}`;
};

const state = {
  data: null,
  sortKey: "days",
};

const byId = (id) => document.getElementById(id);

function setText(id, value) {
  const node = byId(id);
  if (node) node.textContent = value;
}

function sortLeaderboard(rows, key) {
  const sorted = [...rows];
  if (key === "returnRatioAsc") {
    return sorted.sort((a, b) => a.returnRatio - b.returnRatio);
  }
  return sorted.sort((a, b) => {
    if (b[key] !== a[key]) return b[key] - a[key];
    return b.finalNetworth - a.finalNetworth;
  });
}

function renderLeaderboard() {
  const body = byId("leaderboard-body");
  const rows = sortLeaderboard(state.data.leaderboard, state.sortKey);
  const maxNetworth = Math.max(...state.data.leaderboard.map((row) => row.finalNetworth));
  const minNetworth = Math.min(...state.data.leaderboard.map((row) => row.finalNetworth));
  const span = maxNetworth - minNetworth || 1;

  body.innerHTML = rows
    .map((row) => {
      const pct = Math.max(3, ((row.finalNetworth - minNetworth) / span) * 100);
      const tag = row.type === "non_llm" ? "Reference" : "LLM";
      return `
        <tr>
          <td>
            <strong>${row.model}</strong>
            <span class="type-tag">${tag}</span>
          </td>
          <td>${row.framework.replaceAll("_", " ")}</td>
          <td>${formatNumber(row.days)}</td>
          <td class="score-cell">
            ${formatCurrency(row.finalNetworth)}
            <span class="bar-track" aria-hidden="true">
              <span class="bar-fill" style="width:${pct}%"></span>
            </span>
          </td>
          <td>${formatNumber(row.totalSales)}</td>
          <td>${formatNumber(row.soldSkusPerDay, 1)}</td>
          <td>${formatRatio(row.returnRatio)}</td>
        </tr>
      `;
    })
    .join("");
}

function svgElement(name, attrs = {}) {
  const element = document.createElementNS("http://www.w3.org/2000/svg", name);
  Object.entries(attrs).forEach(([key, value]) => element.setAttribute(key, value));
  return element;
}

function renderNetWorthChart() {
  const chartData = state.data.netWorthTrajectories;
  if (!chartData) return;

  const svg = byId("networth-chart");
  const legend = byId("networth-legend");
  const width = 900;
  const height = 420;
  const margin = { top: 28, right: 28, bottom: 52, left: 78 };
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const xMax = chartData.maxDay;
  const yMin = chartData.yMin;
  const yMax = chartData.yMax;
  const palette = [
    "#b42318",
    "#1d4ed8",
    "#047481",
    "#7c3aed",
    "#b45309",
    "#0f766e",
    "#475467",
    "#be185d",
  ];
  const x = (day) => margin.left + (day / xMax) * plotWidth;
  const y = (value) => margin.top + ((yMax - value) / (yMax - yMin)) * plotHeight;
  const xTicks = [0, 60, 120, 180].filter((tick) => tick <= xMax);
  const yTicks = [yMin, 0, 30000, 60000, 90000, 120000, yMax]
    .filter((value, index, values) => value >= yMin && value <= yMax && values.indexOf(value) === index)
    .sort((a, b) => a - b);

  svg.textContent = "";
  legend.textContent = "";

  yTicks.forEach((tick) => {
    const yy = y(tick);
    svg.appendChild(svgElement("line", {
      class: "chart-grid",
      x1: margin.left,
      x2: width - margin.right,
      y1: yy,
      y2: yy,
    }));
    const label = svgElement("text", {
      class: "chart-label",
      x: margin.left - 10,
      y: yy + 4,
      "text-anchor": "end",
    });
    label.textContent = formatCompactCurrency(tick);
    svg.appendChild(label);
  });

  xTicks.forEach((tick) => {
    const xx = x(tick);
    svg.appendChild(svgElement("line", {
      class: "chart-grid",
      x1: xx,
      x2: xx,
      y1: margin.top,
      y2: height - margin.bottom,
    }));
    const label = svgElement("text", {
      class: "chart-label",
      x: xx,
      y: height - 20,
      "text-anchor": "middle",
    });
    label.textContent = `Day ${tick}`;
    svg.appendChild(label);
  });

  svg.appendChild(svgElement("line", {
    class: "chart-axis",
    x1: margin.left,
    x2: margin.left,
    y1: margin.top,
    y2: height - margin.bottom,
  }));
  svg.appendChild(svgElement("line", {
    class: "chart-axis",
    x1: margin.left,
    x2: width - margin.right,
    y1: height - margin.bottom,
    y2: height - margin.bottom,
  }));

  const yTitle = svgElement("text", {
    class: "chart-title-label",
    x: 18,
    y: 24,
  });
  yTitle.textContent = "Net worth";
  svg.appendChild(yTitle);

  chartData.series.forEach((series, index) => {
    const color = palette[index % palette.length];
    const points = series.points.map((point) => `${x(point.day).toFixed(2)},${y(point.netWorth).toFixed(2)}`).join(" ");
    const last = series.points[series.points.length - 1];
    const endedEarly = last.day < xMax;
    const line = svgElement("polyline", {
      class: "chart-line",
      points,
      stroke: color,
      "stroke-width": series.type === "oracle" ? 3.4 : 1.8,
      opacity: series.type === "oracle" ? 1 : 0.78,
    });
    const title = svgElement("title");
    title.textContent = `${series.model} (${series.framework.replaceAll("_", " ")}): ${formatCurrency(series.finalFromTrace)} at Day ${series.days}`;
    line.appendChild(title);
    svg.appendChild(line);

    if (endedEarly) {
      const terminalLine = svgElement("line", {
        class: "chart-terminal-line",
        x1: x(last.day),
        x2: xMax ? x(xMax) : x(last.day),
        y1: y(last.netWorth),
        y2: y(last.netWorth),
        stroke: color,
      });
      const terminalTitle = svgElement("title");
      terminalTitle.textContent = `${series.model}: terminal net worth carried forward after Day ${last.day}.`;
      terminalLine.appendChild(terminalTitle);
      svg.appendChild(terminalLine);
    }

    svg.appendChild(svgElement("circle", {
      class: "chart-point",
      cx: x(last.day),
      cy: y(last.netWorth),
      r: series.type === "oracle" ? 4.2 : 3,
      fill: color,
    }));

    const status = endedEarly ? `ended Day ${last.day}` : `full horizon`;
    const item = document.createElement("div");
    item.className = "legend-item";
    item.innerHTML = `
      <span class="legend-swatch" style="background:${color}"></span>
      <span>
        <span class="legend-name">${series.model}</span>
        <span class="legend-meta">${series.framework.replaceAll("_", " ")} · ${status} · ${formatCurrency(series.finalFromTrace)}</span>
      </span>
    `;
    legend.appendChild(item);
  });
}

function renderSummary() {
  const { benchmark, headline, paper } = state.data;
  setText("paper-meta", `${paper.authors.join(", ")}. arXiv:${paper.arxivId}. DOI ${paper.doi}.`);
  setText("metric-models", formatNumber(benchmark.llmModels));
  setText("metric-runs", formatNumber(benchmark.totalRuns));
  setText("metric-horizon", formatNumber(benchmark.horizonDays));
  setText("metric-full", `${headline.fullHorizonLlms}/${headline.llmCount}`);
  setText("best-llm-networth", formatCurrency(headline.topLlmNetworth));
  setText(
    "best-llm-networth-copy",
    `${headline.topLlmNetworthModel} is the top selected LLM by final net worth.`,
  );
  setText("heuristic-networth", formatCurrency(headline.heuristicNetworth));
  setText("top-sales", formatNumber(headline.topLlmSales));
  setText("top-sales-copy", `${headline.topLlmSalesModel} leads selected LLMs by total unit sales.`);
  setText("selection-rule", `Selection rule: ${benchmark.selectionRule}.`);
}

async function loadBenchmarkData() {
  if (window.RETAILBENCH_BENCHMARK_RESULTS) {
    return window.RETAILBENCH_BENCHMARK_RESULTS;
  }

  const response = await fetch(DATA_URL);
  return response.json();
}

async function init() {
  state.data = await loadBenchmarkData();
  renderSummary();
  renderNetWorthChart();
  renderLeaderboard();

  byId("sort-select").addEventListener("change", (event) => {
    state.sortKey = event.target.value;
    renderLeaderboard();
  });
}

init().catch((error) => {
  console.error(error);
  document.body.insertAdjacentHTML(
    "afterbegin",
    '<div class="load-error">Failed to load benchmark_results.json.</div>',
  );
});
