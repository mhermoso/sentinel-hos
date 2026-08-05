/**
 * Home page Leaflet map for unit (vehicle) positions.
 * Status-colored divIcons, hover tooltips, W/V alert badges; skips null-island (0,0).
 * Exposes focusUnit / setFilters for list + legend controls.
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
  let delegatedWired = false;

  function readUnits(el) {
    const script = document.getElementById("home-units-data");
    if (script && script.textContent && script.textContent.trim()) {
      try {
        return JSON.parse(script.textContent);
      } catch (err) {
        console.warn("HomeMap: bad units JSON from script", err);
      }
    }
    try {
      return JSON.parse(el.getAttribute("data-units") || "[]");
    } catch (err) {
      console.warn("HomeMap: bad units JSON", err);
      return [];
    }
  }

  function mount() {
    const el = document.getElementById("home-map");
    if (!el || typeof L === "undefined") return;
    if (el.dataset.mapMounted === "1") return;
    el.dataset.mapMounted = "1";

    const units = readUnits(el);
    const displayTz = el.getAttribute("data-display-tz") || "America/Chicago";

    const map = L.map(el, { scrollWheelZoom: true });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
    }).addTo(map);

    const listRoot = document.getElementById("home-map-units");
    const listById = new Map();
    if (listRoot) {
      listRoot.querySelectorAll("[data-focus-unit]").forEach((btn) => {
        const id = String(btn.getAttribute("data-focus-unit") || "");
        if (id) listById.set(id, btn);
      });
    }
    const entries = [];

    units.forEach((u) => {
      if (u.latitude == null || u.longitude == null) return;
      const lat = Number(u.latitude);
      const lon = Number(u.longitude);
      if (!Number.isFinite(lat) || !Number.isFinite(lon)) return;
      // Skip null-island / missing GPS placeholders
      if (lat === 0 && lon === 0) return;

      const deviceId = String(u.device_id);
      const unitLabel = u.name || deviceId;
      const driverLabel = u.current_driver_name || u.current_driver_id || "";
      const status = (u.current_status || "UNKNOWN").toUpperCase();
      const key = statusKey(status);
      const timeLabel = formatTimestamp(u.event_timestamp, displayTz);
      const link = `/ui/units/${encodeURIComponent(deviceId)}`;
      const warnCount = Number(u.warning_count) || 0;
      const violCount = Number(u.violation_count) || 0;
      const hasAlert = warnCount > 0 || violCount > 0;
      const badgeKind = violCount > 0 ? "viol" : warnCount > 0 ? "warn" : "";
      const badgeLetter = violCount > 0 ? "V" : warnCount > 0 ? "W" : "";
      const hasDriver = Boolean(u.current_driver_id);
      const knownStatus = key !== "UNKNOWN";

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
        if (u.latest_alert_severity || u.latest_alert_type) {
          alertLine +=
            `<br/>Latest: ${escapeHtml(u.latest_alert_severity || "—")}` +
            (u.latest_alert_type
              ? ` · ${escapeHtml(u.latest_alert_type)}`
              : "");
        }
      }

      const titleLine = driverLabel
        ? `${escapeHtml(unitLabel)} · ${escapeHtml(driverLabel)}`
        : escapeHtml(unitLabel);

      const tooltipHtml =
        `<strong>${titleLine}</strong><br/>` +
        `Status: ${escapeHtml(status)}<br/>` +
        `Time: ${escapeHtml(timeLabel)}<br/>` +
        `${alertLine}<br/>` +
        `<span class="muted">Click for unit detail</span>`;

      marker.bindTooltip(tooltipHtml, {
        direction: "top",
        opacity: 0.95,
        sticky: false,
        className: "hos-marker-tooltip",
      });

      marker.bindPopup(
        `<strong>${escapeHtml(unitLabel)}</strong><br/>` +
          `<span class="mono">${escapeHtml(deviceId)}</span><br/>` +
          (driverLabel
            ? `Driver: ${escapeHtml(driverLabel)}<br/>`
            : "Driver: —<br/>") +
          `Status: ${escapeHtml(status)}<br/>` +
          `${alertLine}<br/>` +
          `<a href="${link}">Open unit</a>`
      );

      entries.push({
        deviceId,
        statusKey: key,
        hasDriver,
        knownStatus,
        warnCount,
        violCount,
        marker,
        listEl: listById.get(deviceId) || null,
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
      filters: {
        statuses: new Set(),
        alerts: new Set(),
        hasDriverOnly: true,
        knownStatusOnly: true,
      },
      focusedId: null,
    };

    wireControls();
    wireDelegatedControls();
    applyFilters({ fit: false });
  }

  function wireControls() {
    const legend = document.querySelector(".map-legend");
    if (legend && !legend.dataset.filterWired) {
      legend.dataset.filterWired = "1";
      legend.addEventListener("click", (ev) => {
        if (!state) return;
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
    if (clearBtn && !clearBtn.dataset.filterWired) {
      clearBtn.dataset.filterWired = "1";
      clearBtn.addEventListener("click", () => {
        clearFilters();
      });
    }
  }

  /** Document-level delegation so toggles / zoom work regardless of mount timing. */
  function wireDelegatedControls() {
    if (delegatedWired) return;
    delegatedWired = true;

    document.addEventListener("click", (ev) => {
      const hasDriverBtn = ev.target.closest("#home-map-has-driver");
      if (hasDriverBtn) {
        if (!state) return;
        state.filters.hasDriverOnly = !state.filters.hasDriverOnly;
        const on = state.filters.hasDriverOnly;
        hasDriverBtn.classList.toggle("is-active", on);
        hasDriverBtn.setAttribute("aria-pressed", on ? "true" : "false");
        applyFilters({ fit: true });
        return;
      }

      const knownStatusBtn = ev.target.closest("#home-map-known-status");
      if (knownStatusBtn) {
        if (!state) return;
        state.filters.knownStatusOnly = !state.filters.knownStatusOnly;
        const on = state.filters.knownStatusOnly;
        knownStatusBtn.classList.toggle("is-active", on);
        knownStatusBtn.setAttribute("aria-pressed", on ? "true" : "false");
        applyFilters({ fit: true });
        return;
      }

      const focusBtn = ev.target.closest("#home-map-units [data-focus-unit]");
      if (focusBtn) {
        const id = focusBtn.getAttribute("data-focus-unit");
        if (id) focusUnit(id);
      }
    });
  }

  function toggleInSet(set, value) {
    if (set.has(value)) set.delete(value);
    else set.add(value);
  }

  function entryMatches(entry, filters) {
    const { statuses, alerts, hasDriverOnly, knownStatusOnly } = filters;
    if (hasDriverOnly && !entry.hasDriver) return false;
    if (knownStatusOnly && !entry.knownStatus) return false;
    if (statuses.size > 0 && !statuses.has(entry.statusKey)) return false;
    if (alerts.size > 0) {
      const hasWarn = alerts.has("warn") && entry.warnCount > 0;
      const hasViol = alerts.has("viol") && entry.violCount > 0;
      if (!hasWarn && !hasViol) return false;
    }
    return true;
  }

  function syncListVisibility(showById) {
    document.querySelectorAll("#home-map-units [data-focus-unit]").forEach((btn) => {
      const id = String(btn.getAttribute("data-focus-unit") || "");
      if (!id || !showById.has(id)) return;
      const show = showById.get(id);
      const row = btn.closest("li") || btn;
      row.classList.toggle("is-hidden", !show);
      if (!show) btn.classList.remove("is-active");
    });
  }

  function applyFilters({ fit }) {
    if (!state) return;
    const { map, entries, filters } = state;
    const visible = [];
    const showById = new Map();

    entries.forEach((entry) => {
      const show = entryMatches(entry, filters);
      const id = String(entry.deviceId);
      showById.set(id, show);
      if (show) {
        if (!map.hasLayer(entry.marker)) entry.marker.addTo(map);
        visible.push(entry);
      } else {
        if (map.hasLayer(entry.marker)) map.removeLayer(entry.marker);
        if (String(state.focusedId) === id) {
          entry.marker.closePopup();
          state.focusedId = null;
        }
      }
    });

    syncListVisibility(showById);
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
      el.textContent = `${total} units with location`;
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

  function focusUnit(deviceId) {
    if (!state || deviceId == null || deviceId === "") return;
    const id = String(deviceId);
    const entry = state.entries.find((e) => String(e.deviceId) === id);
    if (!entry) return;

    // Ensure marker is visible under current filters
    if (!entryMatches(entry, state.filters)) {
      if (!state.map.hasLayer(entry.marker)) entry.marker.addTo(state.map);
    }

    const ll = entry.marker.getLatLng();
    state.map.flyTo(ll, 12, { duration: 0.6 });
    entry.marker.openPopup();
    state.focusedId = id;

    document.querySelectorAll("#home-map-units [data-focus-unit]").forEach((btn) => {
      const btnId = String(btn.getAttribute("data-focus-unit") || "");
      btn.classList.toggle("is-active", btnId === id);
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

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  window.HomeMap = { mount, focusUnit, setFilters, clearFilters };

  // Wire toggles/zoom early; mount still required for map state.
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => {
      wireDelegatedControls();
      mount();
    });
  } else {
    wireDelegatedControls();
    mount();
  }
})();
