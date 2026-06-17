// Fetches the read-only JSON analytics endpoints and renders Plotly charts client-side.
// Decimal fields serialize as strings, so coerce numeric axes via Number(...) before plotting.

async function getJSON(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
}

async function renderPriceHistory() {
  const product = document.getElementById("ph-product").value;
  const data = await getJSON(`/api/analytics/price-history?product=${encodeURIComponent(product)}`);
  const trace = {
    x: data.points.map((p) => p.purchase_date),
    y: data.points.map((p) => Number(p.unit_price)),
    text: data.points.map((p) => p.store_chain),
    mode: "lines+markers",
    type: "scatter",
  };
  Plotly.newPlot("price-history-chart", [trace], { title: `Unit price: ${data.product}` });
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
