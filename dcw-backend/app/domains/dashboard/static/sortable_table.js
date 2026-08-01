/**
 * Shared client-side sortable tables for the HTMX dashboard.
 *
 * Mark a table with data-sortable and th[data-sort-key] + button.sort-btn.
 * Put comparable values on each data row as data-sort-{key}.
 * Optional: data-default-sort, data-default-dir, data-sort-storage, data-row-selector.
 */
(function () {
  const DEFAULT_ROW_SELECTOR = "tr[data-sortable-row]";

  function tablesIn(root) {
    const scope = root && root.querySelectorAll ? root : document;
    if (scope.matches && scope.matches("table[data-sortable]")) {
      return [scope];
    }
    return Array.from(scope.querySelectorAll("table[data-sortable]"));
  }

  function storageKey(table) {
    return table.getAttribute("data-sort-storage") || "";
  }

  function defaultState(table) {
    const key = table.getAttribute("data-default-sort") || "";
    const dirAttr = table.getAttribute("data-default-dir");
    const dir = dirAttr === "asc" ? "asc" : "desc";
    return { key, dir };
  }

  function loadState(table) {
    const fallback = defaultState(table);
    const sk = storageKey(table);
    if (!sk || !fallback.key) return fallback;
    try {
      const raw = sessionStorage.getItem(sk);
      if (!raw) return fallback;
      const parsed = JSON.parse(raw);
      if (!parsed || !parsed.key) return fallback;
      return {
        key: String(parsed.key),
        dir: parsed.dir === "asc" ? "asc" : "desc",
      };
    } catch (err) {
      return fallback;
    }
  }

  function saveState(table, state) {
    const sk = storageKey(table);
    if (!sk) return;
    try {
      sessionStorage.setItem(sk, JSON.stringify(state));
    } catch (err) {
      /* ignore */
    }
  }

  function sortTypeFor(table, key) {
    const escaped = typeof CSS !== "undefined" && CSS.escape ? CSS.escape(key) : key.replace(/"/g, '\\"');
    const th = table.querySelector(`th[data-sort-key="${escaped}"]`);
    return (th && (th.getAttribute("data-sort-as") || th.getAttribute("data-sort-type"))) || "text";
  }

  function defaultDirForKey(table, key) {
    const type = sortTypeFor(table, key);
    if (type === "time" || type === "number") return "desc";
    return "asc";
  }

  function cellValue(row, key) {
    const attr = row.getAttribute("data-sort-" + key);
    if (attr != null) return attr;
    return "";
  }

  function compare(a, b, key, dir, type) {
    const av = cellValue(a, key);
    const bv = cellValue(b, key);
    let cmp = 0;
    if (type === "number") {
      const an = parseFloat(av);
      const bn = parseFloat(bv);
      const aOk = !Number.isNaN(an);
      const bOk = !Number.isNaN(bn);
      if (aOk && bOk) cmp = an < bn ? -1 : an > bn ? 1 : 0;
      else if (aOk) cmp = 1;
      else if (bOk) cmp = -1;
      else cmp = String(av).localeCompare(String(bv), undefined, { sensitivity: "base", numeric: true });
    } else if (type === "time") {
      cmp = av < bv ? -1 : av > bv ? 1 : 0;
    } else {
      cmp = String(av).localeCompare(String(bv), undefined, { sensitivity: "base", numeric: true });
    }
    return dir === "asc" ? cmp : -cmp;
  }

  function updateHeaderUI(table, state) {
    table.querySelectorAll("th[data-sort-key]").forEach((th) => {
      const key = th.getAttribute("data-sort-key");
      const active = key === state.key;
      th.setAttribute(
        "aria-sort",
        active ? (state.dir === "asc" ? "ascending" : "descending") : "none",
      );
      th.classList.toggle("is-sorted", active);
      th.classList.toggle("sort-asc", active && state.dir === "asc");
      th.classList.toggle("sort-desc", active && state.dir === "desc");
      const btn = th.querySelector("button.sort-btn");
      if (btn) {
        const label = btn.getAttribute("data-label") || btn.textContent.replace(/\s*[▲▼]\s*$/, "").trim();
        const arrow = active ? (state.dir === "asc" ? " ▲" : " ▼") : "";
        btn.textContent = label + arrow;
      }
    });
  }

  function applySort(table, state) {
    if (!table || !state || !state.key) return;
    const body = table.tBodies[0] || table.querySelector("tbody");
    if (!body) return;

    const selector = table.getAttribute("data-row-selector") || DEFAULT_ROW_SELECTOR;
    const rows = Array.from(body.querySelectorAll(selector));
    if (!rows.length) {
      updateHeaderUI(table, state);
      return;
    }

    const type = sortTypeFor(table, state.key);
    rows.sort((a, b) => compare(a, b, state.key, state.dir, type));
    const frag = document.createDocumentFragment();
    rows.forEach((row) => frag.appendChild(row));
    body.appendChild(frag);
    updateHeaderUI(table, state);
  }

  function bindTable(table) {
    if (!table || !table.hasAttribute("data-sortable")) return;
    applySort(table, loadState(table));
  }

  function bind(root) {
    tablesIn(root || document).forEach(bindTable);
  }

  function reapply(root) {
    tablesIn(root || document).forEach((table) => {
      applySort(table, loadState(table));
    });
  }

  function onHeaderClick(ev) {
    const btn = ev.target.closest("button.sort-btn");
    if (!btn) return;
    const th = btn.closest("th[data-sort-key]");
    const table = th && th.closest("table[data-sortable]");
    if (!th || !table) return;
    ev.preventDefault();
    const key = th.getAttribute("data-sort-key");
    if (!key) return;
    const state = loadState(table);
    if (state.key === key) {
      state.dir = state.dir === "asc" ? "desc" : "asc";
    } else {
      state.key = key;
      state.dir = defaultDirForKey(table, key);
    }
    saveState(table, state);
    applySort(table, state);
  }

  function onHtmxSwap(ev) {
    if (!ev.target) return;
    bind(ev.target);
  }

  document.addEventListener("click", onHeaderClick);
  document.addEventListener("htmx:afterSwap", onHtmxSwap);

  window.SortableTables = { bind, reapply, applySort, loadState };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => bind(document));
  } else {
    bind(document);
  }
})();
