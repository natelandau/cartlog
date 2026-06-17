// Helpers for the receipt edit form. Loaded on the detail page so they are defined before
// the edit partial is swapped in via HTMX.

function addLineRow() {
  const template = document.getElementById("line-row-template");
  const tbody = document.getElementById("line-rows");
  if (!template || !tbody) return;
  tbody.appendChild(template.content.cloneNode(true));
  updateReconcileHint();  // a new row changes the line-total sum
}

function removeLineRow(button) {
  const row = button.closest("tr");
  if (row) row.remove();
  updateReconcileHint();  // dropping a row changes the line-total sum
}

// Compare the sum of line totals against the entered receipt total and flag mismatches.
function updateReconcileHint() {
  const hint = document.getElementById("reconcile-hint");
  const totalInput = document.querySelector('input[name="total"]');
  if (!hint || !totalInput) return;
  let sum = 0;
  document.querySelectorAll('input[name="line_total"]').forEach(function (input) {
    const value = parseFloat(input.value);
    if (!Number.isNaN(value)) sum += value;
  });
  const total = parseFloat(totalInput.value);
  if (Number.isNaN(total)) {
    hint.textContent = "";
    return;
  }
  // Round to cents so float noise (0.1 + 0.2) does not show a spurious mismatch.
  const diff = Math.round((sum - total) * 100) / 100;
  if (diff === 0) {
    hint.textContent = "lines match total";
    hint.classList.remove("reconcile-off");
  } else {
    const sign = diff > 0 ? "+" : "";
    hint.textContent = "lines off by " + sign + diff.toFixed(2);
    hint.classList.add("reconcile-off");
  }
}

// Recompute on any edit-form input change (event delegation survives HTMX swaps).
document.body.addEventListener("input", function (event) {
  if (event.target.closest(".receipt-edit")) updateReconcileHint();
});
document.body.addEventListener("htmx:afterSwap", updateReconcileHint);
