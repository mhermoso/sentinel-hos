/**
 * Alert calculation drawer — limit gauges + zoomed HOS context strip.
 */
(function () {
  const STATUSES = ["OFF", "SB", "D", "ON"];

  function parseJsonHost(host) {
    const script = host.querySelector('script[type="application/json"]');
    if (!script) return null;
    try {
      return JSON.parse(script.textContent);
    } catch (err) {
      console.warn("AlertDetail: bad JSON", err);
      return null;
    }
  }

  function panelEl() {
    return document.querySelector(".alert-drawer-panel");
  }

  function syncExpandLabel() {
    const panel = panelEl();
    const btn = document.querySelector("[data-drawer-expand]");
    if (!btn) return;
    const full = panel && panel.classList.contains("fullscreen");
    btn.textContent = full ? "Collapse" : "Expand";
  }

  function exitFullscreen() {
    const panel = panelEl();
    if (panel) panel.classList.remove("fullscreen");
    syncExpandLabel();
  }

  function toggleFullscreen() {
    const panel = panelEl();
    if (!panel) return;
    panel.classList.toggle("fullscreen");
    syncExpandLabel();
  }

  function open() {
    const drawer = document.getElementById("alert-drawer");
    if (!drawer) return;
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    syncExpandLabel();
  }

  function close() {
    const drawer = document.getElementById("alert-drawer");
    if (!drawer) return;
    exitFullscreen();
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
  }

  function handleEscape() {
    const drawer = document.getElementById("alert-drawer");
    if (!drawer || !drawer.classList.contains("open")) return;
    const panel = panelEl();
    if (panel && panel.classList.contains("fullscreen")) {
      exitFullscreen();
      return;
    }
    close();
  }

  function showDrawerError(message) {
    const body = document.getElementById("alert-drawer-body");
    if (!body) return;
    body.innerHTML =
      '<div class="alert-detail"><p class="warn-text">' +
      (message || "Failed to load alert calculation details.") +
      "</p>" +
      '<button type="button" class="btn-sm drawer-close" onclick="window.AlertDetail && window.AlertDetail.close()">Close</button></div>';
  }

  function renderGauges(host, clocks) {
    if (!clocks) return;
    const rows = [
      {
        label: "Driving",
        used: clocks.driving_used_h,
        limit: clocks.driving_limit_h,
        remaining: clocks.driving_remaining_h,
      },
      {
        label: "Duty window",
        used: clocks.duty_used_h,
        limit: clocks.duty_limit_h,
        remaining: clocks.duty_remaining_h,
      },
      {
        label: "Weekly",
        used: clocks.weekly_used_h,
        limit: clocks.weekly_limit_h,
        remaining: clocks.weekly_remaining_h,
      },
    ];

    host.innerHTML = "";
    rows.forEach((row) => {
      const pct = Math.min(100, (row.used / (row.limit || 1)) * 100);
      const over = row.used >= row.limit;
      const wrap = document.createElement("div");
      wrap.className = "gauge-row" + (over ? " over" : "");
      wrap.innerHTML = `
        <div class="gauge-label">
          <span>${row.label}</span>
          <span class="mono">${Number(row.used).toFixed(1)}h / ${Number(row.limit).toFixed(0)}h</span>
        </div>
        <div class="gauge-track">
          <div class="gauge-fill" style="width:${pct.toFixed(1)}%"></div>
          <div class="gauge-limit"></div>
        </div>
        <div class="gauge-meta muted">${
          over
            ? (row.used - row.limit).toFixed(1) + "h over"
            : Number(row.remaining).toFixed(1) + "h remaining"
        }</div>
      `;
      host.appendChild(wrap);
    });
  }

  function yForStatus(status, height, padTop, padBottom) {
    const idx = STATUSES.indexOf(status);
    if (idx < 0) return null;
    const usable = height - padTop - padBottom;
    return padTop + (idx * usable) / (STATUSES.length - 1);
  }

  function renderContext(host, payload) {
    if (!payload) return;
    const width = 520;
    const height = 160;
    const padL = 36;
    const padR = 10;
    const padTop = 14;
    const padBottom = 22;
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("role", "img");
    svg.setAttribute("aria-label", "HOS context around alert");

    const bg = document.createElementNS(ns, "rect");
    bg.setAttribute("width", width);
    bg.setAttribute("height", height);
    bg.setAttribute("fill", "#0c1015");
    svg.appendChild(bg);

    STATUSES.forEach((status) => {
      const y = yForStatus(status, height, padTop, padBottom);
      const line = document.createElementNS(ns, "line");
      line.setAttribute("x1", padL);
      line.setAttribute("x2", width - padR);
      line.setAttribute("y1", y);
      line.setAttribute("y2", y);
      line.setAttribute("stroke", "#243040");
      svg.appendChild(line);
      const label = document.createElementNS(ns, "text");
      label.setAttribute("x", 4);
      label.setAttribute("y", y + 3);
      label.setAttribute("fill", "#8b9aab");
      label.setAttribute("font-size", "10");
      label.setAttribute("font-family", "IBM Plex Mono, monospace");
      label.textContent = status;
      svg.appendChild(label);
    });

    const usable = width - padL - padR;
    const events = payload.events || [];
    // Highlight bands under the step line for causal segments
    events.forEach((ev) => {
      if (!ev.highlighted) return;
      const y = yForStatus(ev.status, height, padTop, padBottom);
      if (y == null) return;
      const x0 = padL + (ev.fraction_start || 0) * usable;
      const x1 = padL + (ev.fraction_end || 0) * usable;
      const band = document.createElementNS(ns, "rect");
      band.setAttribute("x", x0.toFixed(1));
      band.setAttribute("y", (y - 6).toFixed(1));
      band.setAttribute("width", Math.max(1, x1 - x0).toFixed(1));
      band.setAttribute("height", "12");
      band.setAttribute("fill", "#e85d5d");
      band.setAttribute("fill-opacity", "0.28");
      svg.appendChild(band);
    });
    const parts = [];
    events.forEach((ev, i) => {
      const y = yForStatus(ev.status, height, padTop, padBottom);
      if (y == null) return;
      const x0 = padL + (ev.fraction_start || 0) * usable;
      const x1 = padL + (ev.fraction_end || 0) * usable;
      if (!parts.length) parts.push(`M ${x0.toFixed(1)} ${y.toFixed(1)}`);
      else parts.push(`L ${x0.toFixed(1)} ${y.toFixed(1)}`);
      parts.push(`H ${x1.toFixed(1)}`);
      if (i + 1 < events.length) {
        const ny = yForStatus(events[i + 1].status, height, padTop, padBottom);
        if (ny != null) parts.push(`V ${ny.toFixed(1)}`);
      }
    });
    if (parts.length) {
      const path = document.createElementNS(ns, "path");
      path.setAttribute("d", parts.join(" "));
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", "#3d9cf0");
      path.setAttribute("stroke-width", "2.5");
      svg.appendChild(path);
    }

    const frac = payload.as_of_fraction != null ? payload.as_of_fraction : 0.75;
    const ax = padL + frac * usable;
    const alertLine = document.createElementNS(ns, "line");
    alertLine.setAttribute("x1", ax);
    alertLine.setAttribute("x2", ax);
    alertLine.setAttribute("y1", padTop - 2);
    alertLine.setAttribute("y2", height - padBottom + 2);
    alertLine.setAttribute("stroke", "#e85d5d");
    alertLine.setAttribute("stroke-width", "1.5");
    alertLine.setAttribute("stroke-dasharray", "3 2");
    svg.appendChild(alertLine);

    let ay = (padTop + height - padBottom) / 2;
    for (let i = 0; i < events.length; i++) {
      const ev = events[i];
      if (frac >= ev.fraction_start && frac < ev.fraction_end) {
        const yy = yForStatus(ev.status, height, padTop, padBottom);
        if (yy != null) ay = yy;
        break;
      }
    }
    const s = 5;
    const diamond = document.createElementNS(ns, "polygon");
    diamond.setAttribute(
      "points",
      `${ax},${ay - s} ${ax + s},${ay} ${ax},${ay + s} ${ax - s},${ay}`
    );
    diamond.setAttribute("fill", "#e85d5d");
    svg.appendChild(diamond);

    const caption = document.createElementNS(ns, "text");
    caption.setAttribute("x", ax);
    caption.setAttribute("y", height - 6);
    caption.setAttribute("fill", "#e85d5d");
    caption.setAttribute("font-size", "9");
    caption.setAttribute("text-anchor", "middle");
    caption.setAttribute("font-family", "IBM Plex Mono, monospace");
    caption.textContent = "alert";
    svg.appendChild(caption);

    host.innerHTML = "";
    host.appendChild(svg);
  }

  function bindMarkerButtons(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-alert-open]").forEach((btn) => {
      if (btn.dataset.alertBound === "1") return;
      btn.dataset.alertBound = "1";
      btn.addEventListener("click", (ev) => {
        ev.preventDefault();
        const driverId = btn.getAttribute("data-driver-id");
        loadFromMarker(driverId, {
          as_of: btn.getAttribute("data-as-of"),
          violation_type: btn.getAttribute("data-violation-type") || "",
          source: btn.getAttribute("data-source") || "backtest",
          severity: btn.getAttribute("data-severity") || "",
          rule_ref: btn.getAttribute("data-rule-ref") || "",
          description: btn.getAttribute("data-description") || "",
        });
      });
    });
  }

  function mount(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-alert-gauges]").forEach((host) => {
      renderGauges(host, parseJsonHost(host));
    });
    scope.querySelectorAll("[data-alert-context]").forEach((host) => {
      renderContext(host, parseJsonHost(host));
    });
    bindMarkerButtons(scope);
  }

  function loadFromMarker(driverId, marker) {
    if (!driverId || !marker || !marker.as_of) {
      open();
      showDrawerError("Missing driver or alert timestamp.");
      return;
    }
    open();
    const body = document.getElementById("alert-drawer-body");
    if (body) {
      body.innerHTML =
        '<p class="muted" style="padding:1.5rem">Loading calculation details…</p>';
    }

    const params = new URLSearchParams({
      as_of: marker.as_of,
      violation_type: marker.violation_type || "",
      source: marker.source || "backtest",
      severity: marker.severity || "",
      rule_ref: marker.rule_ref || "",
      description: marker.description || "",
    });
    const url = `/ui/drivers/${encodeURIComponent(driverId)}/alerts/detail?${params}`;

    if (window.htmx) {
      document.body.addEventListener(
        "htmx:responseError",
        function onErr(ev) {
          if (ev.detail && ev.detail.target && ev.detail.target.id === "alert-drawer-body") {
            showDrawerError(
              "Could not load calculation details (HTTP " +
                (ev.detail.xhr ? ev.detail.xhr.status : "?") +
                ")."
            );
            document.body.removeEventListener("htmx:responseError", onErr);
          }
        }
      );
      htmx.ajax("GET", url, { target: "#alert-drawer-body", swap: "innerHTML" });
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
        showDrawerError("Could not load calculation details: " + err.message);
      });
  }

  window.AlertDetail = {
    open,
    close,
    mount,
    bindMarkerButtons,
    loadFromMarker,
    renderGauges,
    renderContext,
    toggleFullscreen,
    exitFullscreen,
    handleEscape,
  };

  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") handleEscape();
  });

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => bindMarkerButtons());
  } else {
    bindMarkerButtons();
  }
})();
