// Fetches the read-only JSON analytics endpoints and renders Plotly charts client-side.
// Decimal fields serialize as strings, so coerce numeric axes via Number(...) before plotting.

async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
}

const DAY_MS = 86400000;

// Build an x-axis windowed to the data's real span (no empty pre-data years that Plotly
// otherwise invents when auto-ranging a sparse or single-point date axis) and pick the
// finest tick unit that keeps the axis under ~14 labels: weeks for month-scale spans,
// months for year-scale spans, quarters and up beyond that.
function priceHistoryTimeAxis(dates) {
  const times = dates.map((d) => Date.parse(d));
  let min = Math.min(...times);
  let max = Math.max(...times);
  let spanDays = (max - min) / DAY_MS;

  if (spanDays < 1) {
    // A single purchase has no span; give the lone marker a readable ~1-month window.
    min -= 14 * DAY_MS;
    max += 14 * DAY_MS;
    spanDays = 28;
  } else {
    const pad = Math.max(spanDays * 0.03, 1) * DAY_MS; // keep edge markers off the frame
    min -= pad;
    max += pad;
    spanDays += (2 * pad) / DAY_MS;
  }

  const tiers = [
    { dtick: 7 * DAY_MS, approxDays: 7, tickformat: "%b %d" }, // weekly
    { dtick: "M1", approxDays: 30, tickformat: "%b %Y" }, // monthly
    { dtick: "M3", approxDays: 91, tickformat: "%b %Y" }, // quarterly
    { dtick: "M6", approxDays: 182, tickformat: "%b %Y" }, // half-yearly
    { dtick: "M12", approxDays: 365, tickformat: "%Y" }, // yearly
  ];
  const tier = tiers.find((t) => spanDays / t.approxDays <= 14) || tiers[tiers.length - 1];

  return {
    type: "date",
    range: [new Date(min).toISOString(), new Date(max).toISOString()],
    dtick: tier.dtick,
    tickformat: tier.tickformat,
    ticks: "outside",
    showgrid: false, // vertical gridlines are redundant with the ticks
    zeroline: false,
  };
}

async function renderPriceHistory() {
  const product = document.getElementById("ph-product").value;
  const data = await getJSON(`/api/analytics/price-history?product=${encodeURIComponent(product)}`);
  const el = "price-history-chart";

  if (!data.points.length) {
    Plotly.purge(el);
    document.getElementById(el).innerHTML =
      `<p class="text-base-content/60 py-16 text-center">No purchases recorded for “${data.product}”.</p>`;
    return;
  }

  const dates = data.points.map((p) => p.purchase_date);
  const trace = {
    x: dates,
    y: data.points.map((p) => Number(p.unit_price)),
    text: data.points.map((p) => p.store_chain),
    hovertemplate: "%{x|%b %-d, %Y}<br>%{text}<br>$%{y:.2f}<extra></extra>",
    mode: "lines+markers",
    type: "scatter",
    line: { color: "#2563eb", width: 2 },
    marker: { color: "#2563eb", size: 6 },
  };
  const layout = {
    title: { text: `Unit price · ${data.product}`, font: { size: 16 } },
    margin: { t: 40, r: 16, b: 44, l: 56 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
    xaxis: priceHistoryTimeAxis(dates),
    yaxis: {
      title: "Unit price",
      tickprefix: "$",
      gridcolor: "rgba(128,128,128,0.15)", // faint reference lines for reading price levels
      zeroline: false,
      ticks: "outside",
    },
    showlegend: false,
  };
  Plotly.newPlot(el, [trace], layout, { displayModeBar: false, responsive: true });
}

async function renderStoreComparison() {
  const product = document.getElementById("sc-product").value;
  const data = await getJSON(`/api/analytics/store-comparison?product=${encodeURIComponent(product)}`);
  const trace = {
    x: data.rows.map((r) => `${r.store_chain}${r.store_location ? " — " + r.store_location : ""}`),
    y: data.rows.map((r) => Number(r.avg_unit_price)),
    type: "bar",
  };
  Plotly.newPlot("store-comparison-chart", [trace], { title: `Avg unit price by store: ${data.product}` });
}

async function renderCategorySpend() {
  const data = await getJSON(`/api/analytics/category-spend`);
  const trace = {
    labels: data.rows.map((r) => r.category),
    values: data.rows.map((r) => Number(r.total_spend)),
    type: "pie",
  };
  Plotly.newPlot("category-spend-chart", [trace], { title: "Spend by category" });
}
