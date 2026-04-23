"""Constants for Navimow integration."""
from __future__ import annotations
from typing import Final

DOMAIN: Final = "navimow_custom"

OAUTH2_AUTHORIZE: Final = (
    "https://navimow-h5-fra.willand.com/smartHome/login?channel=homeassistant"
)
OAUTH2_TOKEN: Final = "https://navimow-fra.ninebot.com/openapi/oauth/getAccessToken"
OAUTH2_REFRESH: Final | None = None

CLIENT_ID: Final = "homeassistant"
CLIENT_SECRET: Final = "57056e15-722e-42be-bbaa-b0cbfb208a52"

API_BASE_URL: Final = "https://navimow-fra.ninebot.com"

MQTT_BROKER: Final = "mqtt.navimow.com"
MQTT_PORT: Final = 1883
MQTT_USERNAME: Final | None = None
MQTT_PASSWORD: Final | None = None

UPDATE_INTERVAL: Final = 30
MQTT_STALE_SECONDS: Final = 300
HTTP_FALLBACK_MIN_INTERVAL: Final = 60

MOWER_STATUS_TO_ACTIVITY = {
    "idle": "docked",
    "mowing": "mowing",
    "paused": "paused",
    "docked": "docked",
    "charging": "docked",
    "returning": "returning",
    "error": "error",
    "unknown": "error",
}
