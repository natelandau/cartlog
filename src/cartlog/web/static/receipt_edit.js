// Helpers for the receipt edit form. Loaded on the detail page so they are defined before
// the edit partial is swapped in via HTMX.

function addLineRow() {
  const template = document.getElementById("line-row-template");
  const tbody = document.getElementById("line-rows");
  if (!template || !tbody) return;
  tbody.appendChild(template.content.cloneNode(true));
  updateReconcileHint(); // a new item changes the line-total sum
}

function removeLineRow(button) {
  const card = button.closest(".line-item");
  if (card) card.remove();
  updateReconcileHint(); // dropping an item changes the line-total sum
}

// Compare the sum of line totals against the entered receipt total and report it in plain
// language so the editor knows whether the items they entered reconcile with the receipt.
function updateReconcileHint() {
  const box = document.getElementById("reconcile");
  const text = document.getElementById("reconcile-text");
  const totalInput = document.querySelector('input[name="total"]');
  if (!box || !text || !totalInput) return;

  let sum = 0;
  document.querySelectorAll('input[name="line_total"]').forEach(function (input) {
    const value = parseFloat(input.value);
    if (!Number.isNaN(value)) sum += value;
  });
  const total = parseFloat(totalInput.value);
  if (Number.isNaN(total)) {
    box.hidden = true;
    return;
  }
  box.hidden = false;

  const money = (n) => "$" + n.toFixed(2);
  // Round to cents so float noise (0.1 + 0.2) does not show a spurious mismatch.
  const diff = Math.round((sum - total) * 100) / 100;
  if (diff === 0) {
    box.classList.add("is-ok");
    box.classList.remove("is-off");
    text.textContent = "Items add up to " + money(total) + ", matching the receipt total";
  } else {
    box.classList.add("is-off");
    box.classList.remove("is-ok");
    const direction = diff > 0 ? "over" : "under";
    text.textContent =
      "Items total " + money(sum) + ", " + money(Math.abs(diff)) + " " + direction + " the " + money(total) + " receipt total";
  }
}

// Recompute on any edit-form input change (event delegation survives HTMX swaps).
document.body.addEventListener("input", function (event) {
  if (event.target.closest(".receipt-edit")) updateReconcileHint();
});
document.body.addEventListener("htmx:afterSwap", updateReconcileHint);
