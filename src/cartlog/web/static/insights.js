// Shared rendering layer for the Insights page. Chart fragments self-register a renderer via
// Insights.register(key, fn); the dispatcher runs the active view's renderer on initial load
// and after each htmx panel swap. Plotly is lazy-loaded so non-chart/no-JS views pay nothing.
// Decimal fields serialize as strings, so numeric axes are coerced via Number(...) in renderers.

// Idempotent: htmx caches the insights page body (which includes this script tag) and re-runs it
// on a history-restore, so guard against a second `const Insights` redeclaration and against
// double-wiring the listeners below. The renderers Map then survives across restores intact.
window.Insights = window.Insights || (function () {
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

  // Resolve any CSS color (oklch theme tokens included) to a concrete [r,g,b] so Plotly renders
  // on-brand in both themes. Plotly cannot parse oklch and browsers keep computed colors in oklch
  // form, so paint the value onto a 1x1 canvas and read the pixel back.
  const colorProbe = document.createElement("canvas").getContext("2d");
  function cssToRgb(css, fallback) {
    if (!colorProbe || !css) return null;
    colorProbe.fillStyle = fallback; // a valid color so an unparseable css leaves a known value
    colorProbe.fillStyle = css;
    colorProbe.fillRect(0, 0, 1, 1);
    const [r, g, b] = colorProbe.getImageData(0, 0, 1, 1).data;
    return [r, g, b];
  }
  function probe(varName, fallback) {
    if (!colorProbe) return null;
    return cssToRgb(getComputedStyle(document.documentElement).getPropertyValue(varName).trim(), fallback);
  }
  function themeColor(varName, fallback) {
    const rgb = probe(varName, fallback);
    return rgb ? `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})` : fallback;
  }
  // Same resolution but with an alpha channel, for theme-aware gridlines/axis lines that should
  // be a faint tint of the foreground ink rather than a fixed gray that ignores the theme.
  function themeAlpha(varName, alpha, fallback) {
    const rgb = probe(varName, fallback);
    return rgb ? `rgba(${rgb[0]}, ${rgb[1]}, ${rgb[2]}, ${alpha})` : fallback;
  }

  // A categorical palette generated in the theme's own OKLCH system: the lightness and chroma are
  // matched to the brand (so the hues read as muted and on-brand, not generic primaries) while the
  // hue rotates for distinctness. Lightness lifts in dark mode to stay legible on a dark surface.
  // Anchored at the brand green (165), then evenly around the wheel.
  const CATEGORY_HUES = [165, 225, 285, 345, 45, 105, 195, 15];
  const PALETTE_FALLBACK = ["#3a7d5d", "#3f6fa3", "#7a5aa6", "#b0506a", "#c08a3e", "#7e9145"];
  // Detect a dark theme from the actual surface lightness, not a hardcoded theme name, so the
  // palette tracks any current or future theme instead of silently falling back to the light ramp.
  function isDarkSurface() {
    const raw = getComputedStyle(document.documentElement).getPropertyValue("--color-base-100").trim();
    const rgb = cssToRgb(raw, "#ffffff");
    if (!rgb) return false;
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2] < 128; // Rec. 601 luma
  }
  function paletteParams() {
    return isDarkSurface() ? { l: 70, c: 0.11 } : { l: 52, c: 0.105 };
  }
  function categoryColor(i) {
    const { l, c } = paletteParams();
    const hue = CATEGORY_HUES[i % CATEGORY_HUES.length];
    const rgb = cssToRgb(`oklch(${l}% ${c} ${hue})`, PALETTE_FALLBACK[i % PALETTE_FALLBACK.length]);
    return rgb ? `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})` : PALETTE_FALLBACK[i % PALETTE_FALLBACK.length];
  }
  function categoryPalette(count) {
    return Array.from({ length: count }, (_, i) => categoryColor(i));
  }
  // The residual "Other" band: a near-neutral with the brand's faint green tint so it recedes
  // while still belonging to the palette, lifted in dark mode like the rest.
  function categoryMuted() {
    const { l } = paletteParams();
    const rgb = cssToRgb(`oklch(${l + 6}% 0.012 160)`, "#9ca3af");
    return rgb ? `rgb(${rgb[0]}, ${rgb[1]}, ${rgb[2]})` : "#9ca3af";
  }

  // The site's typefaces (declared as @font-face in app.css): Public Sans for body/UI text and
  // Fraunces for display headings. Plotly renders SVG <text> with these once the page has loaded
  // the fonts, so the charts match the rest of the site instead of Plotly's default sans.
  const BODY_FONT = '"Public Sans", system-ui, -apple-system, sans-serif';
  const DISPLAY_FONT = '"Fraunces", Georgia, "Times New Roman", serif';

  // Shared layout: transparent background so the card surface shows through, text colored with
  // the theme ink so titles/ticks stay legible in both light and dark mode, and the site fonts.
  function baseLayout(title) {
    const ink = themeColor("--color-base-content", "#333");
    return {
      title: { text: title, font: { size: 18, color: ink, family: DISPLAY_FONT } },
      font: { color: ink, family: BODY_FONT, size: 13 },
      margin: { t: 44, r: 16, b: 44, l: 56 },
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
    };
  }

  const PLOT_CONFIG = { displayModeBar: false, responsive: true };
  // Shared fill opacity for chart marks (bars, pie slices), standardized across every chart so the
  // muted look is consistent and tweaked in one place.
  const MARK_OPACITY = 0.6;
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

  return { register, ensurePlotly, renderActive, getJSON, showEmpty, themeColor, themeAlpha, categoryPalette, categoryMuted, baseLayout, priceHistoryTimeAxis, PLOT_CONFIG, MARK_OPACITY };
})();

// Wire the page-level listeners exactly once, even if this script re-runs on a history restore.
if (!window.__insightsWired) {
  window.__insightsWired = true;

  // Render the server-embedded initial fragment once the DOM is ready.
  document.addEventListener("DOMContentLoaded", Insights.renderActive);
  // Render the freshly swapped fragment after each htmx swap. Every analysis swap (the dropdown's
  // htmx.ajax and the per-view toolbar forms) targets #insights-panel with innerHTML, so htmx
  // settles on the panel itself; re-render the active chart when it does. afterSettle fires once
  // per swap, so this never double-renders.
  document.body.addEventListener("htmx:afterSettle", function (e) {
    if (e.target && e.target.id === "insights-panel") Insights.renderActive();
  });
  // Back/forward restores the panel from htmx's history cache, which fires historyRestore on the
  // body rather than afterSettle on the panel, so re-render the restored analysis here too.
  document.body.addEventListener("htmx:historyRestore", Insights.renderActive);

  // Charts resolve their colors from CSS theme tokens at draw time, so a theme switch leaves the
  // already-drawn chart with stale (e.g. dark-on-dark, unreadable) colors. The theme toggle flips
  // the data-theme attribute on <html> with no CSS event to listen for, so observe that attribute
  // and re-render the active chart with the freshly resolved palette.
  new MutationObserver(Insights.renderActive).observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
}
