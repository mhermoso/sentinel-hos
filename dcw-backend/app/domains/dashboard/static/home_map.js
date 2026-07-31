/**
 * Home page Leaflet map for driver positions.
 */
(function () {
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

    const map = L.map(el, { scrollWheelZoom: true });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
    }).addTo(map);

    const markers = [];
    positions.forEach((p) => {
      if (p.latitude == null || p.longitude == null) return;
      const name = p.driver_name || p.driver_id;
      const day = (p.event_timestamp || "").slice(0, 10);
      const link = day
        ? `/ui/drivers/${encodeURIComponent(p.driver_id)}?date=${day}`
        : `/ui/drivers/${encodeURIComponent(p.driver_id)}`;
      const marker = L.marker([p.latitude, p.longitude]).addTo(map);
      marker.bindPopup(
        `<strong>${escapeHtml(name)}</strong><br/>` +
          `<span class="mono">${escapeHtml(p.driver_id)}</span><br/>` +
          `Status: ${escapeHtml(p.status || "—")}<br/>` +
          `<a href="${link}">Open day view</a>`
      );
      markers.push(marker);
    });

    if (markers.length) {
      const group = L.featureGroup(markers);
      map.fitBounds(group.getBounds().pad(0.2));
    } else {
      map.setView([39.8283, -98.5795], 4);
    }
  }

  function escapeHtml(value) {
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  window.HomeMap = { mount };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", mount);
  } else {
    mount();
  }
})();
