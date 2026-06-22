// Helpers for the receipt edit form. Loaded on the detail page so they are defined before
// the edit partial is swapped in via HTMX.

// Show only the field group matching the line's "sold by" mode; the other stays in the DOM
// (empty) so the strictly-zipped form columns remain aligned on submit.
function syncSoldBy(select) {
  const card = select.closest(".line-item");
  const mode = select.value;
  card.querySelectorAll("[data-sold-by-group]").forEach(function (group) {
    const active = group.dataset.soldByGroup === mode;
    group.hidden = !active;
    // Clear the inactive group's inputs so a value left over from a prior mode (e.g. a
    // measure unit kept after toggling to item mode) is never submitted and rejected by
    // the form validator. The fields stay in the DOM so the strict-zip columns stay aligned.
    if (!active) {
      group.querySelectorAll("input, select").forEach(function (field) {
        field.value = "";
      });
    }
  });
}

// Initialize every card on load and after htmx swaps.
function syncAllSoldBy() {
  document.querySelectorAll("select.sold-by").forEach(syncSoldBy);
}
document.addEventListener("DOMContentLoaded", syncAllSoldBy);
document.body.addEventListener("htmx:afterSwap", syncAllSoldBy);

function addLineRow() {
  const template = document.getElementById("line-row-template");
  const tbody = document.getElementById("line-rows");
  if (!template || !tbody) return;
  tbody.appendChild(template.content.cloneNode(true));
  // Sync the sold-by toggle on the newly cloned row so the ITEM group starts visible.
  const lastCard = tbody.lastElementChild;
  if (lastCard) {
    const soldBySelect = lastCard.querySelector("select.sold-by");
    if (soldBySelect) syncSoldBy(soldBySelect);
  }
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
