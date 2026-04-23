/**
 * Navimow Map Card - Custom Lovelace card
 * Shows the Navimow mower's live GPS position on an OpenStreetMap.
 *
 * Configuration:
 *   type: custom:navimow-map-card
 *   entity: device_tracker.navimow_i105_location   (required)
 *   zoom: 18                                        (optional, default 18)
 *   title: "Navimow"                                (optional)
 *   hours_to_show: 2                                (optional, show path history)
 */

const LEAFLET_CSS = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
const LEAFLET_JS  = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
const OSM_TILE    = "https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png";
const OSM_ATTR    = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

const MOWER_ICON_SVG = `
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 48 48" width="40" height="40">
  <circle cx="24" cy="24" r="22" fill="#4CAF50" stroke="#fff" stroke-width="3"/>
  <text x="24" y="31" text-anchor="middle" font-size="22" fill="white">🌿</text>
</svg>`;

let _leafletLoaded = null;

function loadLeaflet() {
  if (_leafletLoaded) return _leafletLoaded;
  _leafletLoaded = new Promise((resolve) => {
    if (window.L) { resolve(window.L); return; }

    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = LEAFLET_CSS;
    document.head.appendChild(link);

    const script = document.createElement("script");
    script.src = LEAFLET_JS;
    script.onload = () => resolve(window.L);
    document.head.appendChild(script);
  });
  return _leafletLoaded;
}

class NavimowMapCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._map = null;
    this._marker = null;
    this._polyline = null;
    this._history = [];
    this._hass = null;
    this._config = {};
    this._initialized = false;
    this._unsubscribeHistory = null;
  }

  setConfig(config) {
    if (!config.entity) throw new Error("entity is required");
    this._config = {
      entity: config.entity,
      zoom: config.zoom ?? 18,
      title: config.title ?? "Navimow",
      hours_to_show: config.hours_to_show ?? 2,
    };
    this._render();
  }

  set hass(hass) {
    const prev = this._hass;
    this._hass = hass;

    if (!this._initialized) {
      this._initMap();
      return;
    }

    const state = hass.states[this._config.entity];
    if (!state) return;

    const lat = parseFloat(state.attributes.latitude);
    const lng = parseFloat(state.attributes.longitude);
    if (isNaN(lat) || isNaN(lng)) return;

    this._updateMarker(lat, lng, state);
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host { display: block; }
        ha-card { overflow: hidden; }
        .card-header {
          padding: 12px 16px 0;
          font-size: 1.1em;
          font-weight: 500;
          color: var(--primary-text-color);
          display: flex;
          align-items: center;
          gap: 8px;
        }
        .card-header .title { flex: 1; }
        .card-header .status {
          font-size: 0.8em;
          font-weight: 400;
          color: var(--secondary-text-color);
          background: var(--secondary-background-color);
          border-radius: 12px;
          padding: 2px 8px;
        }
        #map {
          width: 100%;
          height: 350px;
        }
        .no-position {
          height: 80px;
          display: flex;
          align-items: center;
          justify-content: center;
          color: var(--secondary-text-color);
          font-size: 0.9em;
        }
        /* Leaflet CSS needs to live in light DOM but map renders in shadow DOM.
           Re-declare the critical Leaflet rules here. */
        .leaflet-pane, .leaflet-tile, .leaflet-marker-icon,
        .leaflet-marker-shadow, .leaflet-tile-container,
        .leaflet-pane > svg, .leaflet-pane > canvas,
        .leaflet-zoom-box, .leaflet-image-layer,
        .leaflet-layer { position: absolute; }
        .leaflet-container { position: relative; overflow: hidden; background: #ddd; }
        .leaflet-tile { filter: inherit; visibility: hidden; }
        .leaflet-tile-loaded { visibility: inherit; }
        .leaflet-zoom-anim .leaflet-zoom-animated { transition: transform 0.25s cubic-bezier(0,0,0.25,1); }
        .leaflet-pan-anim .leaflet-tile, .leaflet-zoom-anim .leaflet-tile { transition: none; }
        .leaflet-zoom-anim .leaflet-zoom-animated { will-change: transform; }
        .leaflet-zoom-anim .leaflet-zoom-hide { visibility: hidden; }
        .leaflet-overlay-pane svg { -moz-user-select: none; }
        .leaflet-tile-pane { z-index: 2; }
        .leaflet-overlay-pane { z-index: 4; }
        .leaflet-shadow-pane { z-index: 5; }
        .leaflet-marker-pane { z-index: 6; }
        .leaflet-tooltip-pane { z-index: 7; }
        .leaflet-popup-pane { z-index: 8; }
        .leaflet-map-pane canvas { z-index: 1; }
        .leaflet-map-pane svg { z-index: 2; }
        .leaflet-control { position: relative; z-index: 800; pointer-events: visiblePainted; pointer-events: auto; }
        .leaflet-top, .leaflet-bottom { position: absolute; z-index: 1000; pointer-events: none; }
        .leaflet-top { top: 0; }
        .leaflet-right { right: 0; }
        .leaflet-bottom { bottom: 0; }
        .leaflet-left { left: 0; }
        .leaflet-control { float: left; clear: both; }
        .leaflet-right .leaflet-control { float: right; }
        .leaflet-top .leaflet-control { margin-top: 10px; }
        .leaflet-bottom .leaflet-control { margin-bottom: 10px; }
        .leaflet-left .leaflet-control { margin-left: 10px; }
        .leaflet-right .leaflet-control { margin-right: 10px; }
        .leaflet-control-zoom a, .leaflet-control-attribution {
          background: white; border-radius: 2px; color: #333;
          font: 11px/1.5 Arial, Helvetica, sans-serif;
          text-decoration: none;
        }
        .leaflet-control-zoom { box-shadow: 0 1px 5px rgba(0,0,0,.4); border-radius: 4px; }
        .leaflet-control-zoom a {
          width: 26px; height: 26px; line-height: 26px;
          display: block; text-align: center; font-size: 18px; font-weight: bold;
          border-bottom: 1px solid #ccc;
        }
        .leaflet-control-zoom a:last-child { border-bottom: none; }
        .leaflet-control-zoom a:hover { background: #f4f4f4; }
        .leaflet-control-attribution { padding: 0 8px; font-size: 11px; }
        .leaflet-touch .leaflet-control-zoom a { width: 30px; height: 30px; line-height: 30px; }
      </style>
      <ha-card>
        <div class="card-header">
          <span class="title">🌿 ${this._config.title}</span>
          <span class="status" id="status">Waiting...</span>
        </div>
        <div id="map"></div>
        <div class="no-position" id="no-pos" style="display:none">
          No GPS position available yet
        </div>
      </ha-card>
    `;
  }

  async _initMap() {
    if (this._initialized) return;
    this._initialized = true;

    const L = await loadLeaflet();

    const mapEl = this.shadowRoot.getElementById("map");
    if (!mapEl) return;

    const state = this._hass?.states[this._config.entity];
    const lat = parseFloat(state?.attributes?.latitude);
    const lng = parseFloat(state?.attributes?.longitude);
    const center = (isNaN(lat) || isNaN(lng)) ? [56.0, 10.0] : [lat, lng];

    this._map = L.map(mapEl, {
      center,
      zoom: this._config.zoom,
      zoomControl: true,
      attributionControl: true,
    });

    L.tileLayer(OSM_TILE, {
      attribution: OSM_ATTR,
      maxZoom: 22,
      maxNativeZoom: 19,
    }).addTo(this._map);

    // Custom mower icon
    const icon = L.divIcon({
      html: MOWER_ICON_SVG,
      className: "",
      iconSize: [40, 40],
      iconAnchor: [20, 20],
      popupAnchor: [0, -24],
    });

    if (!isNaN(lat) && !isNaN(lng)) {
      this._marker = L.marker([lat, lng], { icon })
        .addTo(this._map)
        .bindPopup(this._popupContent(state));
      this._history.push([lat, lng]);
      this._updateStatus(state);
    } else {
      this.shadowRoot.getElementById("map").style.display = "none";
      this.shadowRoot.getElementById("no-pos").style.display = "flex";
    }

    // Load position history
    this._loadHistory();
  }

  _updateMarker(lat, lng, state) {
    if (!this._map) return;

    const L = window.L;
    if (!L) return;

    // Show map, hide no-pos message
    const mapEl = this.shadowRoot.getElementById("map");
    const noPos = this.shadowRoot.getElementById("no-pos");
    if (mapEl) mapEl.style.display = "";
    if (noPos) noPos.style.display = "none";

    const pos = [lat, lng];

    if (!this._marker) {
      const icon = L.divIcon({
        html: MOWER_ICON_SVG,
        className: "",
        iconSize: [40, 40],
        iconAnchor: [20, 20],
        popupAnchor: [0, -24],
      });
      this._marker = L.marker(pos, { icon })
        .addTo(this._map)
        .bindPopup(this._popupContent(state));
      this._map.setView(pos, this._config.zoom);
    } else {
      this._marker.setLatLng(pos);
      this._marker.setPopupContent(this._popupContent(state));
      this._map.panTo(pos);
    }

    // Append to path
    const last = this._history[this._history.length - 1];
    if (!last || last[0] !== lat || last[1] !== lng) {
      this._history.push(pos);
      if (this._polyline) {
        this._polyline.setLatLngs(this._history);
      } else {
        this._polyline = L.polyline(this._history, {
          color: "#4CAF50",
          weight: 3,
          opacity: 0.7,
        }).addTo(this._map);
      }
    }

    this._updateStatus(state);
  }

  _popupContent(state) {
    if (!state) return "No data";
    const lat = state.attributes.latitude?.toFixed(6);
    const lng = state.attributes.longitude?.toFixed(6);
    const battery = state.attributes.battery ?? "?";
    const status = state.attributes.status ?? state.state ?? "unknown";
    return `
      <b>Navimow</b><br>
      Status: ${status}<br>
      Battery: ${battery}%<br>
      Lat: ${lat}, Lng: ${lng}
    `;
  }

  _updateStatus(state) {
    const el = this.shadowRoot.getElementById("status");
    if (!el || !state) return;
    const status = state.attributes.status ?? state.state ?? "unknown";
    const battery = state.attributes.battery;
    el.textContent = battery != null ? `${status} · 🔋${battery}%` : status;
  }

  async _loadHistory() {
    if (!this._hass || !window.L) return;
    const hours = this._config.hours_to_show;
    if (!hours || hours <= 0) return;

    try {
      const end = new Date();
      const start = new Date(end.getTime() - hours * 3600 * 1000);
      const entity = this._config.entity;
      const url = `/api/history/period/${start.toISOString()}?filter_entity_id=${entity}&end_time=${end.toISOString()}&minimal_response=true`;
      const resp = await this._hass.callApi("GET", url.slice(5)); // strip /api/
      if (!resp || !resp[0]) return;

      const points = resp[0]
        .map((s) => [parseFloat(s.a?.latitude), parseFloat(s.a?.longitude)])
        .filter(([lat, lng]) => !isNaN(lat) && !isNaN(lng));

      if (points.length < 2) return;

      this._history = [...points, ...this._history];
      if (this._polyline) {
        this._polyline.setLatLngs(this._history);
      } else {
        this._polyline = window.L.polyline(this._history, {
          color: "#4CAF50",
          weight: 3,
          opacity: 0.7,
        }).addTo(this._map);
      }
    } catch (e) {
      console.debug("Navimow map: could not load history", e);
    }
  }

  getCardSize() {
    return 5;
  }

  static getStubConfig() {
    return {
      entity: "device_tracker.navimow_i105_location",
      zoom: 18,
      title: "Navimow",
      hours_to_show: 2,
    };
  }
}

customElements.define("navimow-map-card", NavimowMapCard);

window.customCards = window.customCards || [];
window.customCards.push({
  type: "navimow-map-card",
  name: "Navimow Map Card",
  description: "Shows the Navimow mower position on an OpenStreetMap.",
  preview: false,
});
