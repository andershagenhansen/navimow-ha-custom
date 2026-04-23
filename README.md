# Navimow for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://github.com/hacs/integration)

A Home Assistant custom integration for Segway Navimow robotic lawn mowers.

## Features

- **Lawn mower entity** — start, pause, dock, and monitor your mower
- **Battery sensor** — real-time battery percentage
- **GPS device tracker** — live mower position on a map (when supported by firmware)
- **Custom map card** — Leaflet/OpenStreetMap card showing the mower's position and path

## Installation via HACS

1. In HACS, go to **Integrations → Custom repositories**
2. Add `https://github.com/andershagenhansen/navimow-ha-custom` as a custom repository (category: Integration)
3. Search for **Navimow** and install
4. Restart Home Assistant
5. Go to **Settings → Devices & Services → Add Integration → Navimow** and follow the OAuth2 flow

## Map Card

Copy `www/navimow-map-card.js` to your `config/www/` folder, then register it as a Lovelace resource (`/local/navimow-map-card.js`, type: JavaScript module).

Add to a dashboard:

```yaml
type: custom:navimow-map-card
entity: device_tracker.navimow_<your_mower>_location
title: Navimow
zoom: 18
hours_to_show: 2
```

## GPS / Location

Position data is forwarded from the Navimow cloud via MQTT whenever the firmware includes coordinates. All known field names are handled automatically (`lat`/`lng`, `latitude`/`longitude`, nested `position`/`location`/`gps` objects, etc.).

## Requirements

- Home Assistant 2024.1.0+
- Navimow cloud account (OAuth2 login)

## Credits

Based on [NavimowHA](https://github.com/segwaynavimow/NavimowHA) by Segway Navimow.
