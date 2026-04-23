/**
 * Navimow Map Card v4
 * - Satellite (ESRI) or street (CARTO) background
 * - Green dot = session start, Red dot = current position, Blue line = path
 * - Reads battery from sensor entity automatically
 *
 * Config:
 *   type: custom:navimow-map-card
 *   entity: device_tracker.navimow_i105_pbv11_location   (required)
 *   zoom: 19                       (optional, default 19)
 *   title: Navimow                 (optional)
 *   hours_to_show: 2               (optional)
 *   satellite: true                (optional, default true)
 *   center_lat: 55.7664            (optional – dock GPS lat)
 *   center_lon: 12.3456            (optional – dock GPS lon)
 */

const _V = "4"; // increment to bust browser cache

const TILES = {
  satellite: {
    url: "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    attr: "Tiles &copy; Esri &mdash; Source: Esri, DigitalGlobe, GeoEye, USDA, USGS",
    maxNativeZoom: 19,
    maxZoom: 22,
  },
  street: {
    url: "https://{s}.basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}{r}.png",
    attr: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> &copy; <a href="https://carto.com/">CARTO</a>',
    maxNativeZoom: 19,
    maxZoom: 22,
  },
};

const LEAFLET_CSS = `https://unpkg.com/leaflet@1.9.4/dist/leaflet.css?v=${_V}`;
const LEAFLET_JS  = `https://unpkg.com/leaflet@1.9.4/dist/leaflet.js?v=${_V}`;

let _leafletLoaded = null;
function loadLeaflet() {
  if (_leafletLoaded) return _leafletLoaded;
  _leafletLoaded = new Promise((resolve) => {
    if (window.L) { resolve(window.L); return; }
    const link = document.createElement("link");
    link.rel = "stylesheet"; link.href = LEAFLET_CSS;
    document.head.appendChild(link);
    const script = document.createElement("script");
    script.src = LEAFLET_JS;
    script.onload = () => resolve(window.L);
    document.head.appendChild(script);
  });
  return _leafletLoaded;
}

function makeDot(L, color, size = 14) {
  return L.divIcon({
    className: "",
    html: `<div style="width:${size}px;height:${size}px;border-radius:50%;background:${color};border:3px solid #fff;box-shadow:0 0 6px rgba(0,0,0,.7)"></div>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  });
}

// Derive battery sensor entity from tracker entity
// device_tracker.navimow_i105_pbv11_location → sensor.navimow_i105_pbv11_battery
function batteryEntity(trackerEntity) {
  return trackerEntity
    .replace("device_tracker.", "sensor.")
    .replace(/_location$/, "_battery");
}

class NavimowMapCard extends HTMLElement {
  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._map = null;
    this._startMarker = null;
    this._currentMarker = null;
    this._polyline = null;
    this._history = [];
    this._hass = null;
    this._config = {};
    this._initialized = false;
    this._tileLayer = null;
    this._isSatellite = true;
  }

  setConfig(config) {
    if (!config.entity) throw new Error("entity is required");
    this._config = {
      entity: config.entity,
      zoom: config.zoom ?? 19,
      title: config.title ?? "Navimow",
      hours_to_show: config.hours_to_show ?? 2,
      center_lat: config.center_lat ?? null,
      center_lon: config.center_lon ?? null,
    };
    this._isSatellite = config.satellite !== false;
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._initialized) { this._initMap(); return; }
    const state = hass.states[this._config.entity];
    if (!state) return;
    const lat = parseFloat(state.attributes.latitude);
    const lng = parseFloat(state.attributes.longitude);
    if (!isNaN(lat) && !isNaN(lng)) this._updatePosition(lat, lng, state);
    else this._updateStatus(state);
  }

  _render() {
    this.shadowRoot.innerHTML = `
      <style>
        :host{display:block}
        ha-card{overflow:hidden}
        .card-header{
          padding:12px 16px 0;font-size:1.1em;font-weight:500;
          color:var(--primary-text-color);
          display:flex;align-items:center;gap:8px;
        }
        .title{flex:1}
        .status{
          font-size:.8em;font-weight:400;
          color:var(--secondary-text-color);
          background:var(--secondary-background-color);
          border-radius:12px;padding:2px 10px;
        }
        .map-wrap{position:relative}
        #map{width:100%;height:400px}
        .toggle-btn{
          position:absolute;bottom:28px;right:8px;z-index:1000;
          background:rgba(255,255,255,.92);
          border:none;border-radius:6px;
          padding:5px 10px;font-size:12px;font-weight:600;
          cursor:pointer;box-shadow:0 1px 5px rgba(0,0,0,.5);color:#333;
        }
        .toggle-btn:hover{background:#fff}
        .legend{
          display:flex;gap:14px;padding:6px 16px 10px;
          font-size:.8em;color:var(--secondary-text-color);align-items:center;
        }
        .dot{
          width:10px;height:10px;border-radius:50%;
          border:2px solid #fff;box-shadow:0 0 3px rgba(0,0,0,.5);
          display:inline-block;flex-shrink:0;
        }
        .line{display:inline-block;width:18px;height:3px;background:#2196F3;border-radius:2px}
        .no-pos{
          height:80px;display:flex;align-items:center;justify-content:center;
          color:var(--secondary-text-color);font-size:.9em;
        }
        /* Leaflet in shadow DOM */
        .leaflet-pane,.leaflet-tile,.leaflet-marker-icon,.leaflet-marker-shadow,
        .leaflet-tile-container,.leaflet-pane>svg,.leaflet-pane>canvas,
        .leaflet-zoom-box,.leaflet-image-layer,.leaflet-layer{position:absolute}
        .leaflet-container{position:relative;overflow:hidden;background:#1a1a2e}
        .leaflet-tile{filter:inherit;visibility:hidden}
        .leaflet-tile-loaded{visibility:inherit}
        .leaflet-zoom-anim .leaflet-zoom-animated{transition:transform .25s cubic-bezier(0,0,.25,1)}
        .leaflet-pan-anim .leaflet-tile,.leaflet-zoom-anim .leaflet-tile{transition:none}
        .leaflet-zoom-anim .leaflet-zoom-animated{will-change:transform}
        .leaflet-zoom-anim .leaflet-zoom-hide{visibility:hidden}
        .leaflet-overlay-pane svg{-moz-user-select:none}
        .leaflet-tile-pane{z-index:2}.leaflet-overlay-pane{z-index:4}
        .leaflet-shadow-pane{z-index:5}.leaflet-marker-pane{z-index:6}
        .leaflet-tooltip-pane{z-index:7}.leaflet-popup-pane{z-index:8}
        .leaflet-map-pane canvas{z-index:1}.leaflet-map-pane svg{z-index:2}
        .leaflet-control{position:relative;z-index:800;pointer-events:auto}
        .leaflet-top,.leaflet-bottom{position:absolute;z-index:1000;pointer-events:none}
        .leaflet-top{top:0}.leaflet-right{right:0}
        .leaflet-bottom{bottom:0}.leaflet-left{left:0}
        .leaflet-control{float:left;clear:both}
        .leaflet-right .leaflet-control{float:right}
        .leaflet-top .leaflet-control{margin-top:10px}
        .leaflet-bottom .leaflet-control{margin-bottom:10px}
        .leaflet-left .leaflet-control{margin-left:10px}
        .leaflet-right .leaflet-control{margin-right:10px}
        .leaflet-control-zoom a,.leaflet-control-attribution{
          background:#fff;border-radius:2px;color:#333;
          font:11px/1.5 Arial,Helvetica,sans-serif;text-decoration:none}
        .leaflet-control-zoom{box-shadow:0 1px 5px rgba(0,0,0,.4);border-radius:4px}
        .leaflet-control-zoom a{
          width:26px;height:26px;line-height:26px;
          display:block;text-align:center;font-size:18px;font-weight:bold;
          border-bottom:1px solid #ccc}
        .leaflet-control-zoom a:last-child{border-bottom:none}
        .leaflet-control-zoom a:hover{background:#f4f4f4}
        .leaflet-control-attribution{padding:0 8px;font-size:11px}
        /* Popup always light regardless of HA dark mode */
        .leaflet-popup-content-wrapper,.leaflet-popup-tip{
          background:#fff!important;color:#333!important;
          box-shadow:0 2px 8px rgba(0,0,0,.4)!important}
        .leaflet-popup-content{font-size:13px;line-height:1.6;margin:10px 14px}
        .leaflet-popup-content b{color:#222}
      </style>
      <ha-card>
        <div class="card-header">
          <span class="title">🌿 ${this._config.title}</span>
          <span class="status" id="status">—</span>
        </div>
        <div class="map-wrap">
          <div id="map"></div>
          <button class="toggle-btn" id="toggle"></button>
        </div>
        <div class="legend">
          <span class="dot" style="background:#4CAF50"></span>Start
          <span class="dot" style="background:#f44336"></span>Current
          <span class="line"></span>Path
        </div>
        <div class="no-pos" id="no-pos" style="display:none">No position yet — waiting for mower to start</div>
      </ha-card>`;
    this.shadowRoot.getElementById("toggle")
      .addEventListener("click", () => this._toggleLayer());
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

    // Center priority: config override → entity position → HA home
    let center;
    if (this._config.center_lat && this._config.center_lon) {
      center = [this._config.center_lat, this._config.center_lon];
    } else if (!isNaN(lat) && !isNaN(lng)) {
      center = [lat, lng];
    } else {
      center = [
        this._hass?.config?.latitude ?? 56.0,
        this._hass?.config?.longitude ?? 10.0,
      ];
    }

    this._map = L.map(mapEl, { center, zoom: this._config.zoom });
    this._applyTiles(L);

    if (!isNaN(lat) && !isNaN(lng)) {
      this._addStartDot(L, lat, lng);
      this._currentMarker = L.marker([lat, lng], { icon: makeDot(L, "#f44336", 16) })
        .addTo(this._map);
      this._history.push([lat, lng]);
      this._updateStatus(state);
    } else {
      mapEl.style.display = "none";
      this.shadowRoot.getElementById("no-pos").style.display = "flex";
    }

    this._loadHistory();
  }

  _addStartDot(L, lat, lng) {
    if (this._startMarker) return;
    this._startMarker = L.marker([lat, lng], { icon: makeDot(L, "#4CAF50", 14) })
      .bindTooltip("Start", { permanent: false, direction: "top" })
      .addTo(this._map);
  }

  _updatePosition(lat, lng, state) {
    if (!this._map) return;
    const L = window.L;
    if (!L) return;

    this.shadowRoot.getElementById("map").style.display = "";
    this.shadowRoot.getElementById("no-pos").style.display = "none";

    const pos = [lat, lng];
    this._addStartDot(L, lat, lng);

    if (!this._currentMarker) {
      this._currentMarker = L.marker(pos, { icon: makeDot(L, "#f44336", 16) }).addTo(this._map);
      this._map.setView(pos, this._config.zoom);
    } else {
      this._currentMarker.setLatLng(pos);
      this._map.panTo(pos);
    }

    const last = this._history[this._history.length - 1];
    if (!last || last[0] !== lat || last[1] !== lng) {
      this._history.push(pos);
      if (this._polyline) {
        this._polyline.setLatLngs(this._history);
      } else {
        this._polyline = L.polyline(this._history, { color: "#2196F3", weight: 3, opacity: .9 })
          .addTo(this._map);
      }
    }
    this._updateStatus(state);
  }

  _applyTiles(L) {
    if (!L) L = window.L;
    if (!L || !this._map) return;
    if (this._tileLayer) this._map.removeLayer(this._tileLayer);
    const t = this._isSatellite ? TILES.satellite : TILES.street;
    this._tileLayer = L.tileLayer(t.url, {
      attribution: t.attr,
      maxNativeZoom: t.maxNativeZoom,
      maxZoom: t.maxZoom,
    }).addTo(this._map);
    const btn = this.shadowRoot.getElementById("toggle");
    if (btn) btn.textContent = this._isSatellite ? "🗺 Street" : "🛰 Satellite";
  }

  _toggleLayer() {
    this._isSatellite = !this._isSatellite;
    this._applyTiles(window.L);
  }

  _updateStatus(state) {
    const el = this.shadowRoot.getElementById("status");
    if (!el) return;

    // Battery from dedicated sensor entity
    const battSensor = this._hass?.states[batteryEntity(this._config.entity)];
    const battery = battSensor ? Math.round(parseFloat(battSensor.state)) : null;

    const mowerState = state?.attributes?.status ?? state?.state ?? "—";
    el.textContent = battery != null && !isNaN(battery)
      ? `${mowerState} · 🔋${battery}%`
      : mowerState;
  }

  async _loadHistory() {
    if (!this._hass || !window.L || !this._config.hours_to_show) return;
    try {
      const end = new Date();
      const start = new Date(end - this._config.hours_to_show * 3600000);
      const url = `history/period/${start.toISOString()}?filter_entity_id=${this._config.entity}&end_time=${end.toISOString()}&minimal_response=true`;
      const resp = await this._hass.callApi("GET", url);
      if (!resp?.[0]) return;
      const pts = resp[0]
        .map(s => [parseFloat(s.a?.latitude), parseFloat(s.a?.longitude)])
        .filter(([a, b]) => !isNaN(a) && !isNaN(b));
      if (pts.length < 2) return;
      this._history = [...pts, ...this._history];
      if (this._startMarker) this._startMarker.setLatLng(pts[0]);
      else this._addStartDot(window.L, pts[0][0], pts[0][1]);
      if (this._polyline) {
        this._polyline.setLatLngs(this._history);
      } else {
        this._polyline = window.L.polyline(this._history, { color: "#2196F3", weight: 3, opacity: .9 })
          .addTo(this._map);
      }
    } catch (e) {
      console.debug("Navimow map history error", e);
    }
  }

  getCardSize() { return 6; }

  static getStubConfig() {
    return {
      entity: "device_tracker.navimow_i105_pbv11_location",
      zoom: 19,
      title: "Navimow",
      hours_to_show: 2,
      satellite: true,
    };
  }
}

customElements.define("navimow-map-card", NavimowMapCard);
window.customCards = window.customCards || [];
window.customCards.push({
  type: "navimow-map-card",
  name: "Navimow Map Card",
  description: "Live mower position on satellite imagery.",
  preview: false,
});
