/**
 * Compatibility shim — alerts sorting is handled by sortable_table.js.
 * Preserves window.AlertsSort for existing alerts.html callers.
 */
(function () {
  function bind() {
    window.SortableTables && window.SortableTables.bind(document.getElementById("alerts-table"));
  }

  function reapply() {
    const table = document.getElementById("alerts-table");
    if (table && window.SortableTables) {
      window.SortableTables.reapply(table);
    }
  }

  window.AlertsSort = { bind, reapply, applySort: reapply };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", bind);
  } else {
    bind();
  }
})();
