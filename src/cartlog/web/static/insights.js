// Shared rendering layer for the Insights page. Chart fragments self-register a renderer via
// Insights.register(key, fn); the dispatcher runs the active view's renderer on initial load
// and after each htmx panel swap. Plotly is lazy-loaded so non-chart/no-JS views pay nothing.
// Decimal fields serialize as strings, so numeric axes are coerced via Number(...) in renderers.

const Insights = (function () {
  const renderers = new Map();
  let plotlyPromise = null;

  async function getJSON(url) {
    const response = await fetch(url);
    if (!response.ok) throw new Error(`Request failed: ${response.status}`);
    return response.json();
  }

  // Inject Plotly once, on first chart render; cache the in-flight promise so rapid view
  // switches never inject the 4.5 MB script twice.
  function ensurePlotly() {
    if (window.Plotly) return Promise.resolve(window.Plotly);
    if (plotlyPromise) return plotlyPromise;
    plotlyPromise = new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "/static/plotly.min.js";
      s.onload = () => resolve(window.Plotly);
      s.onerror = () => {
        // Drop the cached rejection so a later render can retry instead of failing forever.
        plotlyPromise = null;
        reject(new Error("Failed to load Plotly"));
      };
      document.head.appendChild(s);
    });
    return plotlyPromise;
  }

  function register(key, fn) {
    renderers.set(key, fn);
  }

  // Replace a chart container with a centered empty-state message. Purges any prior Plotly
  // chart first (renderers await ensurePlotly, so Plotly is loaded here) and uses textContent
  // so caller-supplied data never reaches innerHTML.
  function showEmpty(el, message) {
    if (window.Plotly) Plotly.purge(el);
    const msg = document.createElement("p");
    msg.className = "text-base-content/60 py-16 text-center";
    msg.textContent = message;
    el.replaceChildren(msg);
  }

  // Render whatever analysis is currently mounted in the panel. A view whose root carries no
  // registered renderer (a server-rendered/no-JS analysis) is a no-op.
  function renderActive() {
    const panel = document.getElementById("insights-panel");
    if (!panel) return;
    const root = panel.querySelector("[data-insight-view]");
    if (!root) return;
    const fn = renderers.get(root.getAttribute("data-insight-view"));
    if (fn) fn(root);
  }

  // Resolve a CSS custom property (oklch theme tokens) to a concrete rgb string so Plotly
  // renders on-brand in both light and dark mode. Plotly cannot parse oklch and browsers keep
  // computed colors in oklch form, so paint the value onto a 1x1 canvas and read the pixel back.
  const colorProbe = document.createElement("canvas").getContext("2d");
  function themeColor(varName, fallback) {
    if (!colorProbe) return fallback;
    const raw = getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
    if (!raw) return fallback;
    colorProbe.fillStyle = fallback;
    colorProbe.fillStyle = raw;
    colorProbe.fillRect(0, 0, 1, 1);
    const [r, g, b] = colorProbe.getImageData(0, 0, 1, 1).data;
    return `rgb(${r}, ${g}, ${b})`;
  }

  // Categorical palette for multi-slice charts, drawn from the theme tokens.
  function cartlogPalette() {
    return ["--color-primary", "--color-accent", "--color-info", "--color-success", "--color-secondary", "--color-warning"].map(
      (v, i) => themeColor(v, ["#3a7d5d", "#c08a3e", "#4a90c2", "#4f9e6a", "#6b8f86", "#c2952e"][i])
    );
  }

  // Shared layout: transparent background so the card surface shows through, text colored with
  // the theme ink so titles/ticks stay legible in both light and dark mode.
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
  // invents when auto-ranging a sparse or single-point date axis) and pick the finest tick unit
  // that keeps the axis under ~14 labels: weeks for month-scale spans, months for year-scale
  // spans, quarters and up beyond that.
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

  return { register, ensurePlotly, renderActive, getJSON, showEmpty, themeColor, cartlogPalette, baseLayout, priceHistoryTimeAxis, PLOT_CONFIG };
})();

// Render the server-embedded initial fragment once the DOM is ready.
document.addEventListener("DOMContentLoaded", Insights.renderActive);
// Render the freshly swapped fragment after each select-driven htmx swap. htmx fires afterSettle
// on each inserted node (the analysis root carries data-insight-view), not on #insights-panel, so
// match the root element; it matches exactly once per swap, avoiding a double render.
document.body.addEventListener("htmx:afterSettle", function (e) {
  if (e.target.matches && e.target.matches("[data-insight-view]")) Insights.renderActive();
});
// Back/forward restores the panel from htmx's history cache, which fires historyRestore on the
// body rather than afterSettle on the panel, so re-render the restored analysis here too.
document.body.addEventListener("htmx:historyRestore", Insights.renderActive);
