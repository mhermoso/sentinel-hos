/**
 * Inline SVG HOS status grid — OFF / SB / D / ON / UNKNOWN over a 24h local day.
 * PC plots on OFF (striped); YM plots on ON (striped). UNKNOWN has its own lane.
 * Markers: WARNING triangle, VIOLATION diamond, CRITICAL circle.
 */
(function () {
  const STATUSES = ["OFF", "SB", "D", "ON", "UNKNOWN"];
  const LANE_LABELS = {
    OFF: "OFF",
    SB: "SB",
    D: "D",
    ON: "ON",
    UNKNOWN: "UNK",
  };
  const LANE_FOR_STATUS = {
    OFF: "OFF",
    SB: "SB",
    D: "D",
    ON: "ON",
    UNKNOWN: "UNKNOWN",
    PC: "OFF",
    YM: "ON",
  };
  const Y_PAD_TOP = 18;
  const Y_PAD_BOTTOM = 28;
  const X_PAD_LEFT = 44;
  const X_PAD_RIGHT = 12;
  const HEIGHT = 248;
  const WIDTH = 900;
  const STRIPE_BAND = 10;

  function laneFor(ev) {
    if (ev.lane && STATUSES.indexOf(ev.lane) >= 0) return ev.lane;
    return LANE_FOR_STATUS[ev.status] || null;
  }

  function yForStatus(status) {
    const idx = STATUSES.indexOf(status);
    if (idx < 0) return null;
    const usable = HEIGHT - Y_PAD_TOP - Y_PAD_BOTTOM;
    const step = usable / (STATUSES.length - 1);
    return Y_PAD_TOP + idx * step;
  }

  function xForHour(hour, width) {
    const usable = width - X_PAD_LEFT - X_PAD_RIGHT;
    return X_PAD_LEFT + (hour / 24) * usable;
  }

  function parsePayload(host) {
    const attr = host.getAttribute("data-payload");
    if (attr) {
      try {
        return JSON.parse(attr);
      } catch (err) {
        console.warn("HOSGrid: bad data-payload", err);
      }
    }
    const script = host.querySelector('script[type="application/json"]');
    if (script) {
      try {
        return JSON.parse(script.textContent);
      } catch (err) {
        console.warn("HOSGrid: bad JSON script", err);
      }
    }
    return null;
  }

  function buildStepPath(events, width) {
    if (!events || !events.length) return "";
    const GAP_HOURS = 0.02;
    const parts = [];
    let lastEndHour = null;
    let lastY = null;
    for (let i = 0; i < events.length; i++) {
      const ev = events[i];
      const lane = laneFor(ev);
      const y = lane ? yForStatus(lane) : null;
      if (y == null) continue;
      const x0 = xForHour(ev.hour_of_day, width);
      const endHour = Math.min(24, ev.hour_of_day + (ev.duration_seconds || 0) / 3600);
      const x1 = xForHour(endHour, width);
      const contiguous =
        lastEndHour != null && ev.hour_of_day - lastEndHour <= GAP_HOURS;

      if (!parts.length || !contiguous) {
        parts.push(`M ${x0.toFixed(2)} ${y.toFixed(2)}`);
      } else if (lastY != null && Math.abs(lastY - y) > 0.01) {
        parts.push(`V ${y.toFixed(2)}`);
      }
      parts.push(`H ${x1.toFixed(2)}`);
      lastEndHour = endHour;
      lastY = y;
    }
    return parts.join(" ");
  }

  function ensureDefs(svg, ns) {
    let defs = svg.querySelector("defs");
    if (defs) return defs;
    defs = document.createElementNS(ns, "defs");

    const mkPattern = (id, color) => {
      const pattern = document.createElementNS(ns, "pattern");
      pattern.setAttribute("id", id);
      pattern.setAttribute("patternUnits", "userSpaceOnUse");
      pattern.setAttribute("width", "8");
      pattern.setAttribute("height", "8");
      pattern.setAttribute("patternTransform", "rotate(45)");
      const bg = document.createElementNS(ns, "rect");
      bg.setAttribute("width", "8");
      bg.setAttribute("height", "8");
      bg.setAttribute("fill", color);
      bg.setAttribute("fill-opacity", "0.22");
      const stripe = document.createElementNS(ns, "rect");
      stripe.setAttribute("width", "3");
      stripe.setAttribute("height", "8");
      stripe.setAttribute("fill", color);
      stripe.setAttribute("fill-opacity", "0.85");
      pattern.appendChild(bg);
      pattern.appendChild(stripe);
      defs.appendChild(pattern);
    };

    mkPattern("hos-stripe-pc", "#6b9bd1");
    mkPattern("hos-stripe-ym", "#c4a35a");
    svg.appendChild(defs);
    return defs;
  }

  function drawExemptionBands(svg, ns, events, width) {
    if (!events || !events.length) return;
    events.forEach((ev) => {
      if (ev.status !== "PC" && ev.status !== "YM") return;
      const lane = laneFor(ev);
      const y = lane ? yForStatus(lane) : null;
      if (y == null) return;
      const x0 = xForHour(ev.hour_of_day, width);
      const endHour = Math.min(24, ev.hour_of_day + (ev.duration_seconds || 0) / 3600);
      const x1 = xForHour(endHour, width);
      const w = Math.max(1, x1 - x0);
      const rect = document.createElementNS(ns, "rect");
      rect.setAttribute("x", x0.toFixed(2));
      rect.setAttribute("y", (y - STRIPE_BAND / 2).toFixed(2));
      rect.setAttribute("width", w.toFixed(2));
      rect.setAttribute("height", STRIPE_BAND);
      rect.setAttribute(
        "fill",
        ev.status === "PC" ? "url(#hos-stripe-pc)" : "url(#hos-stripe-ym)"
      );
      rect.setAttribute("class", `hos-exempt hos-exempt-${ev.status.toLowerCase()}`);
      svg.appendChild(rect);
    });
  }

  function ensureTooltip() {
    let tip = document.getElementById("hos-tooltip");
    if (!tip) {
      tip = document.createElement("div");
      tip.id = "hos-tooltip";
      tip.className = "hos-tooltip";
      document.body.appendChild(tip);
    }
    return tip;
  }

  function formatLocalClock(iso) {
    if (!iso) return "";
    // Day builder already encodes display-TZ wall clock in local_* ISO strings.
    // Prefer that wall clock so browser TZ cannot shift the tooltip.
    const match = String(iso).match(/T(\d{2}:\d{2}:\d{2})/);
    if (match) return match[1];
    try {
      const d = new Date(iso);
      if (Number.isNaN(d.getTime())) return String(iso).slice(11, 19);
      return d.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
        hour12: false,
      });
    } catch (err) {
      return String(iso).slice(11, 19);
    }
  }

  function formatLocalDateTime(iso) {
    if (!iso) return "";
    const match = String(iso).match(/^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})/);
    if (match) return `${match[1]} ${match[2]}`;
    return String(iso).replace("T", " ").slice(0, 19);
  }

  function segmentTipText(ev) {
    const start = formatLocalClock(ev.local_timestamp);
    const end = formatLocalClock(ev.local_end_timestamp);
    const status = ev.status || "?";
    const dur = ev.duration_hhmm || "";
    let line = `${status}  ${start} – ${end}`;
    if (dur) line += `\nDuration ${dur}`;
    if (ev.distance_label) line += `\n${ev.distance_label}`;
    else if (ev.distance_mi > 0) line += `\n${Number(ev.distance_mi).toFixed(1)} mi`;
    return line;
  }

  function severityFill(sev) {
    const s = (sev || "").toUpperCase();
    if (s === "CRITICAL") return "#e85d5d";
    if (s === "VIOLATION") return "#f0a06a";
    return "#e6b84d";
  }

  function appendMarkerShape(g, ns, sev, x, y, fill) {
    const s = (sev || "").toUpperCase();
    if (s === "CRITICAL") {
      const circle = document.createElementNS(ns, "circle");
      circle.setAttribute("cx", x);
      circle.setAttribute("cy", y);
      circle.setAttribute("r", "6");
      circle.setAttribute("fill", fill);
      circle.setAttribute("stroke", "#0c1015");
      circle.setAttribute("stroke-width", "1");
      g.appendChild(circle);
      return;
    }
    if (s === "WARNING") {
      const tri = document.createElementNS(ns, "polygon");
      const h = 7;
      tri.setAttribute(
        "points",
        `${x},${y - h} ${x + h},${y + h * 0.7} ${x - h},${y + h * 0.7}`
      );
      tri.setAttribute("fill", fill);
      tri.setAttribute("stroke", "#0c1015");
      tri.setAttribute("stroke-width", "1");
      g.appendChild(tri);
      return;
    }
    // VIOLATION (default): diamond
    const diamond = document.createElementNS(ns, "polygon");
    const side = 6;
    diamond.setAttribute(
      "points",
      `${x},${y - side} ${x + side},${y} ${x},${y + side} ${x - side},${y}`
    );
    diamond.setAttribute("fill", fill);
    diamond.setAttribute("stroke", "#0c1015");
    diamond.setAttribute("stroke-width", "1");
    g.appendChild(diamond);
  }

  function drawSegmentHitAreas(svg, ns, events, width, tip) {
    if (!events || !events.length) return;
    events.forEach((ev) => {
      const lane = laneFor(ev);
      const y = lane ? yForStatus(lane) : null;
      if (y == null) return;
      const x0 = xForHour(ev.hour_of_day, width);
      const endHour = Math.min(24, ev.hour_of_day + (ev.duration_seconds || 0) / 3600);
      const x1 = xForHour(endHour, width);
      const w = Math.max(4, x1 - x0);
      const hit = document.createElementNS(ns, "rect");
      hit.setAttribute("x", x0.toFixed(2));
      hit.setAttribute("y", (y - 10).toFixed(2));
      hit.setAttribute("width", w.toFixed(2));
      hit.setAttribute("height", "20");
      hit.setAttribute("fill", "transparent");
      hit.style.cursor = "default";
      const title = segmentTipText(ev);
      hit.addEventListener("mouseenter", (e) => {
        tip.textContent = title;
        tip.classList.add("visible");
        tip.style.left = `${e.clientX + 12}px`;
        tip.style.top = `${e.clientY + 12}px`;
      });
      hit.addEventListener("mousemove", (e) => {
        tip.style.left = `${e.clientX + 12}px`;
        tip.style.top = `${e.clientY + 12}px`;
      });
      hit.addEventListener("mouseleave", () => {
        tip.classList.remove("visible");
      });
      svg.appendChild(hit);
    });
  }

  function render(host, payload) {
    const width = WIDTH;
    const height = HEIGHT;
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
    svg.setAttribute("role", "img");
    svg.setAttribute(
      "aria-label",
      `HOS status grid for ${payload.date || "day"}`
    );

    const bg = document.createElementNS(ns, "rect");
    bg.setAttribute("width", width);
    bg.setAttribute("height", height);
    bg.setAttribute("fill", "#0c1015");
    svg.appendChild(bg);

    ensureDefs(svg, ns);

    STATUSES.forEach((status) => {
      const y = yForStatus(status);
      const line = document.createElementNS(ns, "line");
      line.setAttribute("x1", X_PAD_LEFT);
      line.setAttribute("x2", width - X_PAD_RIGHT);
      line.setAttribute("y1", y);
      line.setAttribute("y2", y);
      line.setAttribute("stroke", "#243040");
      line.setAttribute("stroke-width", "1");
      svg.appendChild(line);

      const label = document.createElementNS(ns, "text");
      label.setAttribute("x", 8);
      label.setAttribute("y", y + 4);
      label.setAttribute("fill", "#8b9aab");
      label.setAttribute("font-size", "11");
      label.setAttribute("font-family", "IBM Plex Mono, monospace");
      if (status === "UNKNOWN") {
        label.setAttribute("fill", "#6b7a8a");
      }
      label.textContent = LANE_LABELS[status] || status;
      svg.appendChild(label);
    });

    for (let h = 0; h <= 24; h++) {
      const x = xForHour(h, width);
      const tick = document.createElementNS(ns, "line");
      tick.setAttribute("x1", x);
      tick.setAttribute("x2", x);
      tick.setAttribute("y1", Y_PAD_TOP - 4);
      tick.setAttribute("y2", height - Y_PAD_BOTTOM + 4);
      tick.setAttribute("stroke", h % 6 === 0 ? "#2a3644" : "#1a2430");
      tick.setAttribute("stroke-width", "1");
      svg.appendChild(tick);

      if (h % 2 === 0 && h < 24) {
        const t = document.createElementNS(ns, "text");
        t.setAttribute("x", x);
        t.setAttribute("y", height - 8);
        t.setAttribute("fill", "#6b7a8a");
        t.setAttribute("font-size", "10");
        t.setAttribute("font-family", "IBM Plex Mono, monospace");
        t.setAttribute("text-anchor", "middle");
        t.textContent = String(h);
        svg.appendChild(t);
      }
    }

    drawExemptionBands(svg, ns, payload.events || [], width);

    const pathD = buildStepPath(payload.events || [], width);
    if (pathD) {
      const path = document.createElementNS(ns, "path");
      path.setAttribute("d", pathD);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", "#3d9cf0");
      path.setAttribute("stroke-width", "3");
      path.setAttribute("stroke-linejoin", "miter");
      path.setAttribute("stroke-linecap", "butt");
      path.setAttribute("pointer-events", "none");
      svg.appendChild(path);
    }

    const tip = ensureTooltip();
    drawSegmentHitAreas(svg, ns, payload.events || [], width, tip);

    const markers = payload.alert_markers || [];
    markers.forEach((m) => {
      let y = (Y_PAD_TOP + (HEIGHT - Y_PAD_BOTTOM)) / 2;
      const hour = m.hour_of_day;
      const events = payload.events || [];
      for (let i = 0; i < events.length; i++) {
        const ev = events[i];
        const end = ev.hour_of_day + (ev.duration_seconds || 0) / 3600;
        if (hour >= ev.hour_of_day && hour < end) {
          const lane = laneFor(ev);
          const yy = lane ? yForStatus(lane) : null;
          if (yy != null) y = yy;
          break;
        }
      }
      const x = xForHour(hour, width);
      const g = document.createElementNS(ns, "g");
      g.style.cursor = "pointer";

      const fill = severityFill(m.severity);
      const tick = document.createElementNS(ns, "line");
      tick.setAttribute("x1", x);
      tick.setAttribute("x2", x);
      tick.setAttribute("y1", Y_PAD_TOP - 2);
      tick.setAttribute("y2", height - Y_PAD_BOTTOM + 2);
      tick.setAttribute("stroke", fill);
      tick.setAttribute("stroke-opacity", "0.35");
      tick.setAttribute("stroke-width", "1");
      tick.setAttribute("stroke-dasharray", "2 3");
      g.appendChild(tick);
      appendMarkerShape(g, ns, m.severity, x, y, fill);

      const when = formatLocalDateTime(m.local_timestamp || m.as_of || "");
      const title = `${m.violation_type} (${m.severity})\n${m.description || ""}\n${when} · ${m.source || ""}`;
      g.addEventListener("mouseenter", (ev) => {
        tip.textContent = title;
        tip.classList.add("visible");
        tip.style.left = `${ev.clientX + 12}px`;
        tip.style.top = `${ev.clientY + 12}px`;
      });
      g.addEventListener("mousemove", (ev) => {
        tip.style.left = `${ev.clientX + 12}px`;
        tip.style.top = `${ev.clientY + 12}px`;
      });
      g.addEventListener("mouseleave", () => {
        tip.classList.remove("visible");
      });
      g.addEventListener("click", () => {
        tip.classList.remove("visible");
        const driverId = host.getAttribute("data-driver-id") || payload.driver_id;
        if (window.AlertDetail && driverId) {
          window.AlertDetail.loadFromMarker(driverId, m);
        }
      });

      svg.appendChild(g);
    });

    host.innerHTML = "";
    host.appendChild(svg);
  }

  function mountAll(root) {
    const scope = root || document;
    scope.querySelectorAll("[data-hos-grid]").forEach((host) => {
      const payload = parsePayload(host);
      if (payload) render(host, payload);
    });
  }

  window.HOSGrid = { mountAll, render };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", () => mountAll());
  } else {
    mountAll();
  }
})();
