/**
 * Home page Leaflet map for driver positions.
 * Status-colored divIcons, hover tooltips, W/V alert badges; skips null-island (0,0).
 * Exposes focusDriver / setFilters for list + legend controls.
 */
(function () {
  const STATUS_CLASS = {
    OFF: "status-off",
    PC: "status-off",
    SB: "status-sb",
    D: "status-d",
    ON: "status-on",
    YM: "status-on",
    UNKNOWN: "status-unknown",
  };

  /** Map raw HOS status to legend filter group (PC→OFF, YM→ON). */
  function statusKey(status) {
    const s = (status || "UNKNOWN").toUpperCase();
    if (s === "PC") return "OFF";
    if (s === "YM") return "ON";
    if (STATUS_CLASS[s]) return s;
    return "UNKNOWN";
  }

  let state = null;

  function mount() {
    const el = document.getElementById("home-map");
    if (!el || typeof L === "undefined") return;
    if (el.dataset.mapMounted === "1") return;
    el.dataset.mapMounted = "1";

    let positions = [];
    try {
      positions = JSON.parse(el.getAttribute("data-positions") || "[]");
    } catch (err) {
      console.warn("HomeMap: bad positions JSON", err);
    }
    const displayTz = el.getAttribute("data-display-tz") || "America/Chicago";

    const map = L.map(el, { scrollWheelZoom: true });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
    }).addTo(map);

    const listRoot = document.getElementById("home-map-drivers");
    const listById = new Map();
    if (listRoot) {
      listRoot.querySelectorAll("[data-focus-driver]").forEach((btn) => {
        const id = btn.getAttribute("data-focus-driver");
        if (id) listById.set(id, btn);
      });
    }
    const entries = [];

    positions.forEach((p) => {
      if (p.latitude == null || p.longitude == null) return;
      const lat = Number(p.latitude);
      const lon = Number(p.longitude);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
      // Skip null-island / missing GPS placeholders
      if (lat === 0 && lon === 0) return;

      const name = p.driver_name || p.driver_id;
      const status = (p.status || "UNKNOWN").toUpperCase();
      const key = statusKey(status);
      const day = formatDateInTz(p.event_timestamp, displayTz);
      const timeLabel = formatTimestamp(p.event_timestamp, displayTz);
      const link = day
        ? `/ui/drivers/${encodeURIComponent(p.driver_id)}?date=${day}`
        : `/ui/drivers/${encodeURIComponent(p.driver_id)}`;
      const warnCount = Number(p.warning_count) || 0;
      const violCount = Number(p.violation_count) || 0;
      const hasAlert = warnCount > 0 || violCount > 0;
      const badgeKind = violCount > 0 ? "viol" : warnCount > 0 ? "warn" : "";
      const badgeLetter = violCount > 0 ? "V" : warnCount > 0 ? "W" : "";

      const icon = L.divIcon({
        className: "hos-marker-wrap",
        html:
          `<div class="hos-marker ${STATUS_CLASS[status] || "status-unknown"}">` +
          (hasAlert
            ? `<span class="hos-marker-badge ${badgeKind}" title="${badgeLetter}">${badgeLetter}</span>`
            : "") +
          `</div>`,
        iconSize: [22, 22],
        iconAnchor: [11, 11],
        tooltipAnchor: [0, -12],
      });

      const marker = L.marker([lat, lon], { icon }).addTo(map);

      let alertLine = "Alerts (30d): none";
      if (hasAlert) {
        alertLine = `Alerts (30d): ${warnCount}W / ${violCount}V`;
        if (p.latest_alert_severity || p.latest_alert_type) {
          alertLine +=
            `<br/>Latest: ${escapeHtml(p.latest_alert_severity || "—")}` +
            (p.latest_alert_type
              ? ` · ${escapeHtml(p.latest_alert_type)}`
              : "");
        }
      }

      const tooltipHtml =
        `<strong>${escapeHtml(name)}</strong><br/>` +
        `Status: ${escapeHtml(status)}<br/>` +
        `Time: ${escapeHtml(timeLabel)}<br/>` +
        `${alertLine}<br/>` +
        `<span class="muted">Click for day view</span>`;

      marker.bindTooltip(tooltipHtml, {
        direction: "top",
        opacity: 0.95,
        sticky: false,
        className: "hos-marker-tooltip",
      });

      marker.bindPopup(
        `<strong>${escapeHtml(name)}</strong><br/>` +
          `<span class="mono">${escapeHtml(p.driver_id)}</span><br/>` +
          `Status: ${escapeHtml(status)}<br/>` +
          `${alertLine}<br/>` +
          `<a href="${link}">Open day view</a>`
      );

      entries.push({
        driverId: p.driver_id,
        statusKey: key,
        hasName: Boolean(p.driver_name),
        warnCount,
        violCount,
        marker,
        listEl: listById.get(p.driver_id) || null,
      });
    });

    if (entries.length) {
      const group = L.featureGroup(entries.map((e) => e.marker));
      map.fitBounds(group.getBounds().pad(0.2));
    } else {
      map.setView([39.8283, -98.5795], 4);
    }

    state = {
      map,
      entries,
      filters: { statuses: new Set(), alerts: new Set(), namedOnly: true },
      focusedId: null,
    };

    wireControls();
    applyFilters({ fit: false });
  }

  function wireControls() {
    const legend = document.querySelector(".map-legend");
    if (legend) {
      legend.addEventListener("click", (ev) => {
        const btn = ev.target.closest("[data-filter-status], [data-filter-alert]");
        if (!btn || !legend.contains(btn)) return;
        const status = btn.getAttribute("data-filter-status");
        const alert = btn.getAttribute("data-filter-alert");
        if (status) {
          toggleInSet(state.filters.statuses, status);
          const on = state.filters.statuses.has(status);
          btn.classList.toggle("is-active", on);
          btn.setAttribute("aria-pressed", on ? "true" : "false");
        }
        if (alert) {
          toggleInSet(state.filters.alerts, alert);
          const on = state.filters.alerts.has(alert);
          btn.classList.toggle("is-active", on);
          btn.setAttribute("aria-pressed", on ? "true" : "false");
        }
        applyFilters({ fit: true });
        updateFilterChrome();
      });
    }

    const clearBtn = document.getElementById("home-map-clear-filters");
    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        clearFilters();
      });
    }

    const namedBtn = document.getElementById("home-map-named-only");
    if (namedBtn) {
      namedBtn.addEventListener("click", () => {
        state.filters.namedOnly = !state.filters.namedOnly;
        const on = state.filters.namedOnly;
        namedBtn.classList.toggle("is-active", on);
        namedBtn.setAttribute("aria-pressed", on ? "true" : "false");
        applyFilters({ fit: true });
      });
    }

    const listRoot = document.getElementById("home-map-drivers");
    if (listRoot) {
      listRoot.addEventListener("click", (ev) => {
        const btn = ev.target.closest("[data-focus-driver]");
        if (!btn || !listRoot.contains(btn)) return;
        const id = btn.getAttribute("data-focus-driver");
        if (id) focusDriver(id);
      });
    }
  }

  function toggleInSet(set, value) {
    if (set.has(value)) set.delete(value);
    else set.add(value);
  }

  function entryMatches(entry, filters) {
    const { statuses, alerts, namedOnly } = filters;
    if (namedOnly && !entry.hasName) return false;
    if (statuses.size > 0 && !statuses.has(entry.statusKey)) return false;
    if (alerts.size > 0) {
      const hasWarn = alerts.has("warn") && entry.warnCount > 0;
      const hasViol = alerts.has("viol") && entry.violCount > 0;
      if (!hasWarn && !hasViol) return false;
    }
    return true;
  }

  function applyFilters({ fit }) {
    if (!state) return;
    const { map, entries, filters } = state;
    const visible = [];

    entries.forEach((entry) => {
      const show = entryMatches(entry, filters);
      if (show) {
        if (!map.hasLayer(entry.marker)) entry.marker.addTo(map);
        visible.push(entry);
      } else {
        if (map.hasLayer(entry.marker)) map.removeLayer(entry.marker);
        if (state.focusedId === entry.driverId) {
          entry.marker.closePopup();
          state.focusedId = null;
        }
      }
      if (entry.listEl) {
        const row = entry.listEl.closest("li") || entry.listEl;
        row.classList.toggle("is-hidden", !show);
        if (!show) entry.listEl.classList.remove("is-active");
      }
    });

    updateVisibleCount(visible.length, entries.length);

    if (fit && visible.length) {
      const group = L.featureGroup(visible.map((e) => e.marker));
      map.fitBounds(group.getBounds().pad(0.2));
    }
  }

  function updateVisibleCount(shown, total) {
    const el = document.getElementById("home-map-visible-count");
    if (!el) return;
    if (shown === total) {
      el.textContent = `${total} drivers with location`;
    } else {
      el.textContent = `${shown} of ${total} shown`;
    }
  }

  function filtersActive() {
    if (!state) return false;
    return state.filters.statuses.size > 0 || state.filters.alerts.size > 0;
  }

  function updateFilterChrome() {
    const clearBtn = document.getElementById("home-map-clear-filters");
    if (clearBtn) {
      clearBtn.hidden = !filtersActive();
    }
  }

  function clearFilters() {
    if (!state) return;
    state.filters.statuses.clear();
    state.filters.alerts.clear();
    document.querySelectorAll(".map-legend-btn.is-active").forEach((btn) => {
      btn.classList.remove("is-active");
      btn.setAttribute("aria-pressed", "false");
    });
    applyFilters({ fit: true });
    updateFilterChrome();
  }

  function setFilters({ statuses, alerts } = {}) {
    if (!state) return;
    state.filters.statuses =
      statuses instanceof Set ? statuses : new Set(statuses || []);
    state.filters.alerts =
      alerts instanceof Set ? alerts : new Set(alerts || []);

    document.querySelectorAll(".map-legend-btn[data-filter-status]").forEach((btn) => {
      const key = btn.getAttribute("data-filter-status");
      const on = state.filters.statuses.has(key);
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });
    document.querySelectorAll(".map-legend-btn[data-filter-alert]").forEach((btn) => {
      const key = btn.getAttribute("data-filter-alert");
      const on = state.filters.alerts.has(key);
      btn.classList.toggle("is-active", on);
      btn.setAttribute("aria-pressed", on ? "true" : "false");
    });

    applyFilters({ fit: true });
    updateFilterChrome();
  }

  function focusDriver(driverId) {
    if (!state || !driverId) return;
    const entry = state.entries.find((e) => e.driverId === driverId);
    if (!entry) return;

    // Ensure marker is visible under current filters
    if (!entryMatches(entry, state.filters)) {
      // Temporarily show by clearing conflicting filters? Plan: list is filtered —
      // only focus visible rows. If somehow called for hidden, still fly.
      if (!state.map.hasLayer(entry.marker)) entry.marker.addTo(state.map);
    }

    const ll = entry.marker.getLatLng();
    state.map.flyTo(ll, 12, { duration: 0.6 });
    entry.marker.openPopup();
    state.focusedId = driverId;

    state.entries.forEach((e) => {
      if (e.listEl) {
        e.listEl.classList.toggle("is-active", e.driverId === driverId);
      }
    });
  }

  function formatTimestamp(value, timeZone) {
    if (!value) return "—";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value).replace("T", " ").slice(0, 19);
    try {
      return d.toLocaleString(undefined, {
        timeZone: timeZone || "America/Chicago",
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    } catch (err) {
      return d.toLocaleString(undefined, {
        year: "numeric",
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      });
    }
  }

  function formatDateInTz(value, timeZone) {
    if (!value) return "";
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return String(value).slice(0, 10);
    try {
      // en-CA → YYYY-MM-DD
      return d.toLocaleDateString("en-CA", { timeZone: timeZone || "America/Chicago" });
    } catch (err) {
      return d.toISOString().slice(0, 10);
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  window.HomeMap = { mount, focusDriver, setFilters, clearFilters };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
