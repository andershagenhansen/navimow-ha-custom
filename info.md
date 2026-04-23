## Navimow Integration

Integrates Segway Navimow robotic lawn mowers into Home Assistant via the official Navimow cloud API and MQTT.

**Entities created per mower:**
- `lawn_mower.*` — control (start / pause / dock) and state
- `sensor.*_battery` — battery percentage
- `device_tracker.*_location` — GPS position (when firmware provides it)
