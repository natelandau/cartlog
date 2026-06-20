// Fetches the read-only JSON analytics endpoints and renders Plotly charts client-side.
// Decimal fields serialize as strings, so coerce numeric axes via Number(...) before plotting.

async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
}

// Resolve a CSS custom property (the theme tokens are oklch) to a concrete rgb string so
// Plotly renders on-brand and adapts to the active light/dark theme. Plotly cannot parse
// oklch, and modern browsers keep computed colors in oklch form, so paint the value onto a
// 1x1 canvas and read the pixel back as rgb. An unparseable value keeps the fallback fill.
const _colorProbe = document.createElement("canvas").getContext("2d");
function themeColor(varName, fallback) {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
  if (!raw) return fallback;
  _colorProbe.fillStyle = fallback;
  _colorProbe.fillStyle = raw;
  _colorProbe.fillRect(0, 0, 1, 1);
  const [r, g, b] = _colorProbe.getImageData(0, 0, 1, 1).data;
  return `rgb(${r}, ${g}, ${b})`;
}

// Categorical palette for multi-slice charts, drawn from the theme tokens.
function cartlogPalette() {
  return ["--color-primary", "--color-accent", "--color-info", "--color-success", "--color-secondary", "--color-warning"].map(
    (v, i) => themeColor(v, ["#3a7d5d", "#c08a3e", "#4a90c2", "#4f9e6a", "#6b8f86", "#c2952e"][i])
  );
}

// Shared layout: transparent background so the card surface shows through, and text colored
// with the theme ink so titles/ticks stay legible in both light and dark mode.
function baseLayout(title) {
  const ink = themeColor("--color-base-content", "#333");
  return {
    title: { text: title, font: { size: 16, color: ink } },
    font: { color: ink },
    margin: { t: 44, r: 16, b: 44, l: 56 },
    paper_bgcolor: "rgba(0,0,0,0)",
    plot_bgcolor: "rgba(0,0,0,0)",
  };
}

const PLOT_CONFIG = { displayModeBar: false, responsive: true };

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
  const pine = themeColor("--color-primary", "#3a7d5d");
  const trace = {
    x: dates,
    y: data.points.map((p) => Number(p.unit_price)),
    text: data.points.map((p) => p.store_chain),
    hovertemplate: "%{x|%b %-d, %Y}<br>%{text}<br>$%{y:.2f}<extra></extra>",
    mode: "lines+markers",
    type: "scatter",
    line: { color: pine, width: 2 },
    marker: { color: pine, size: 6 },
  };
  const layout = {
    ...baseLayout(`Unit price · ${data.product}`),
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
  Plotly.newPlot(el, [trace], layout, PLOT_CONFIG);
}

async function renderStoreComparison() {
  const product = document.getElementById("sc-product").value;
  const data = await getJSON(`/api/analytics/store-comparison?product=${encodeURIComponent(product)}`);
  const trace = {
    x: data.rows.map((r) => `${r.store_chain}${r.store_location ? ", " + r.store_location : ""}`),
    y: data.rows.map((r) => Number(r.avg_unit_price)),
    type: "bar",
    marker: { color: themeColor("--color-primary", "#3a7d5d") },
  };
  const layout = { ...baseLayout(`Avg unit price by store: ${data.product}`), yaxis: { tickprefix: "$" } };
  Plotly.newPlot("store-comparison-chart", [trace], layout, PLOT_CONFIG);
}

async function renderCategorySpend() {
  const data = await getJSON(`/api/analytics/category-spend`);
  const trace = {
    labels: data.rows.map((r) => r.category),
    values: data.rows.map((r) => Number(r.total_spend)),
    type: "pie",
    marker: { colors: cartlogPalette() },
  };
  Plotly.newPlot("category-spend-chart", [trace], baseLayout("Spend by category"), PLOT_CONFIG);
}
