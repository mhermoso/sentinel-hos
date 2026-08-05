/**
 * Driver-day GPS route map (Leaflet).
 * Status-colored polylines + W/V/C alert markers; drawer Expand / Full page.
 * Exposes RouteMap.mount / open / close / toggleFullscreen / loadForDriverDay.
 */
(function () {
  const STATUS_COLORS = {
    OFF: "#8b9aab",
    SB: "#6b8cae",
    D: "#3d9cf0",
    ON: "#e6b84d",
    UNKNOWN: "#6b7280",
  };

  function severityFill(sev) {
    const s = (sev || "").toUpperCase();
    if (s === "CRITICAL") return "#e85d5d";
    if (s === "VIOLATION") return "#f0a06a";
    return "#e6b84d";
  }

  function markerSvg(sev) {
    const fill = severityFill(sev);
    const s = (sev || "").toUpperCase();
    if (s === "CRITICAL") {
      return (
        `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 18 18">` +
        `<circle cx="9" cy="9" r="6" fill="${fill}" stroke="#0c1015" stroke-width="1"/></svg>`
      );
    }
    if (s === "WARNING") {
      return (
        `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 18 18">` +
        `<polygon points="9,2 16,15 2,15" fill="${fill}" stroke="#0c1015" stroke-width="1"/></svg>`
      );
    }
    return (
      `<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" viewBox="0 0 18 18">` +
      `<polygon points="9,2 16,9 9,16 2,9" fill="${fill}" stroke="#0c1015" stroke-width="1"/></svg>`
    );
  }

  function parseJsonHost(host) {
    const script = host.querySelector('script[type="application/json"]');
    if (!script) return null;
    try {
      return JSON.parse(script.textContent);
    } catch (err) {
      console.warn("RouteMap: bad JSON", err);
      return null;
    }
  }

  function panelEl() {
    return document.querySelector("#route-drawer .alert-drawer-panel");
  }

  function syncExpandLabel() {
    const panel = panelEl();
    const btn = document.querySelector("#route-drawer [data-drawer-expand]");
    if (!btn) return;
    const full = panel && panel.classList.contains("fullscreen");
    btn.textContent = full ? "Collapse" : "Expand";
  }

  function exitFullscreen() {
    const panel = panelEl();
    if (panel) panel.classList.remove("fullscreen");
    syncExpandLabel();
    invalidateMaps();
  }

  function toggleFullscreen() {
    const panel = panelEl();
    if (!panel) return;
    panel.classList.toggle("fullscreen");
    syncExpandLabel();
    setTimeout(invalidateMaps, 160);
  }

  function open() {
    const drawer = document.getElementById("route-drawer");
    if (!drawer) return;
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    syncExpandLabel();
  }

  function close() {
    const drawer = document.getElementById("route-drawer");
    if (!drawer) return;
    exitFullscreen();
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
  }

  function handleEscape() {
    const drawer = document.getElementById("route-drawer");
    if (!drawer || !drawer.classList.contains("open")) return;
    const panel = panelEl();
    if (panel && panel.classList.contains("fullscreen")) {
      exitFullscreen();
      return;
    }
    close();
  }

  const maps = new WeakMap();

  function invalidateMaps() {
    document.querySelectorAll("[data-route-map]").forEach((el) => {
      const m = maps.get(el);
      if (m) m.invalidateSize();
    });
  }

  function mount(root) {
    const scope = root || document;
    if (typeof L === "undefined") {
      console.warn("RouteMap: Leaflet not loaded");
      return;
    }
    scope.querySelectorAll("[data-route-map]").forEach((host) => {
      if (host.dataset.mapMounted === "1") {
        const existing = maps.get(host);
        if (existing) setTimeout(() => existing.invalidateSize(), 50);
        return;
      }
      const payload = parseJsonHost(host);
      if (!payload) return;
      host.dataset.mapMounted = "1";

      const meta = payload.meta || {};
      const noteEl = host.parentElement && host.parentElement.querySelector("[data-route-note]");
      if (noteEl && meta.coverage_note) {
        noteEl.textContent = meta.coverage_note;
        noteEl.hidden = false;
      }

      const map = L.map(host, { scrollWheelZoom: true });
      L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 18,
        attribution:
          '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
      }).addTo(map);
      maps.set(host, map);

      const bounds = [];
      const segments = payload.segments || [];
      segments.forEach((seg) => {
        const latlngs = [
          [seg.lat1, seg.lon1],
          [seg.lat2, seg.lon2],
        ];
        const color = seg.color || STATUS_COLORS[seg.status] || STATUS_COLORS.UNKNOWN;
        L.polyline(latlngs, { color, weight: 4, opacity: 0.9 }).addTo(map);
        bounds.push(latlngs[0], latlngs[1]);
      });

      const driverId = meta.driver_id || host.getAttribute("data-driver-id") || "";
      (payload.alerts || []).forEach((a) => {
        if (a.lat == null || a.lon == null) return;
        const icon = L.divIcon({
          className: "route-alert-icon",
          html: markerSvg(a.severity),
          iconSize: [18, 18],
          iconAnchor: [9, 9],
        });
        const marker = L.marker([a.lat, a.lon], { icon }).addTo(map);
        marker.bindTooltip(
          `${a.violation_type || "alert"} (${a.severity || ""})`,
          { direction: "top" }
        );
        marker.on("click", () => {
          if (window.AlertDetail && driverId) {
            window.AlertDetail.loadFromMarker(driverId, {
              as_of: a.as_of,
              violation_type: a.violation_type || "",
              source: a.source || "backtest",
              severity: a.severity || "",
              rule_ref: a.rule_ref || "",
              description: a.description || "",
            });
          }
        });
        bounds.push([a.lat, a.lon]);
      });

      if (bounds.length) {
        map.fitBounds(bounds, { padding: [28, 28], maxZoom: 14 });
      } else {
        map.setView([39.5, -98.35], 4);
      }
      setTimeout(() => map.invalidateSize(), 80);
    });
  }

  function loadRouteDetail(url) {
    open();
    const body = document.getElementById("route-drawer-body");
    if (body) {
      body.innerHTML =
        '<p class="muted" style="padding:1.5rem">Loading route map…</p>';
    }

    if (window.htmx) {
      htmx.ajax("GET", url, { target: "#route-drawer-body", swap: "innerHTML" });
      return;
    }

    fetch(url)
      .then((res) => {
        if (!res.ok) throw new Error("HTTP " + res.status);
        return res.text();
      })
      .then((html) => {
        if (body) {
          body.innerHTML = html;
          mount(body);
        }
      })
      .catch((err) => {
        if (body) {
          body.innerHTML =
            '<div class="alert-detail"><p class="warn-text">Could not load route map: ' +
            err.message +
            "</p></div>";
        }
      });
  }

  function loadForDriverDay(driverId, dateStr) {
    if (!driverId) return;
    const params = new URLSearchParams();
    if (dateStr) params.set("date", dateStr);
    const url =
      `/ui/drivers/${encodeURIComponent(driverId)}/route/detail` +
      (params.toString() ? `?${params}` : "");
    loadRouteDetail(url);
  }

  function loadForDeviceDay(deviceId, dateStr) {
    if (!deviceId) return;
    const params = new URLSearchParams();
    if (dateStr) params.set("date", dateStr);
    const url =
      `/ui/units/${encodeURIComponent(deviceId)}/route/detail` +
      (params.toString() ? `?${params}` : "");
    loadRouteDetail(url);
  }

  function bindOpenButtons(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-route-open]").forEach((btn) => {
      if (btn.dataset.routeBound === "1") return;
      btn.dataset.routeBound = "1";
      btn.addEventListener("click", (ev) => {
        ev.preventDefault();
        const deviceId = btn.getAttribute("data-device-id");
        const driverId = btn.getAttribute("data-driver-id");
        const dateStr = btn.getAttribute("data-date") || "";
        if (deviceId) {
          loadForDeviceDay(deviceId, dateStr);
          return;
        }
        loadForDriverDay(driverId, dateStr);
      });
    });
  }

  window.RouteMap = {
    mount,
    open,
    close,
    toggleFullscreen,
    exitFullscreen,
    handleEscape,
    loadForDriverDay,
    loadForDeviceDay,
    bindOpenButtons,
  };

  document.addEventListener("keydown", (ev) => {
    if (ev.key !== "Escape") return;
    // Prefer alert drawer if open
    const alertDrawer = document.getElementById("alert-drawer");
    if (alertDrawer && alertDrawer.classList.contains("open")) return;
    handleEscape();
  });

  document.addEventListener("DOMContentLoaded", () => {
    bindOpenButtons();
    mount();
  });
})();
