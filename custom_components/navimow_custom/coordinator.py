"""DataUpdateCoordinator for Navimow integration."""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from mower_sdk.api import MowerAPI
from mower_sdk.models import (
    Device,
    DeviceAttributesMessage,
    DeviceStateMessage,
    DeviceStatus,
)
from mower_sdk.sdk import NavimowSDK

from .const import (
    DOMAIN,
    HTTP_FALLBACK_MIN_INTERVAL,
    MQTT_STALE_SECONDS,
    UPDATE_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


def _extract_position(payload: dict[str, Any]) -> dict[str, float] | None:
    """Try every reasonable field-name combination to find lat/lng."""

    def _to_float(v: Any) -> float | None:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    def _from_dict(d: dict[str, Any]) -> dict[str, float] | None:
        lat = _to_float(
            d.get("lat") or d.get("latitude") or d.get("Lat") or d.get("Latitude")
        )
        lng = _to_float(
            d.get("lng") or d.get("lon") or d.get("longitude")
            or d.get("Lng") or d.get("Lon") or d.get("Longitude")
        )
        if lat is not None and lng is not None:
            return {"lat": lat, "lng": lng}
        return None

    result = _from_dict(payload)
    if result:
        return result

    for key in ("position", "location", "gps", "loc", "pos", "coords", "coordinate", "geo"):
        val = payload.get(key)
        if isinstance(val, dict):
            result = _from_dict(val)
            if result:
                return result

    params = payload.get("params")
    if isinstance(params, dict):
        result = _extract_position(params)
        if result:
            return result

    value = payload.get("value")
    if isinstance(value, dict):
        result = _extract_position(value)
        if result:
            return result

    return None


def _extract_local_coords(payload: dict[str, Any]) -> dict[str, float] | None:
    """Extract postureX/Y/Theta from a location MQTT payload item."""
    def _f(v: Any) -> float | None:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    x = _f(payload.get("postureX"))
    y = _f(payload.get("postureY"))
    theta = _f(payload.get("postureTheta"))
    if x is None or y is None:
        return None
    return {"posture_x": x, "posture_y": y, "posture_theta": theta or 0.0}


def _xy_to_latlon(
    posture_x: float, posture_y: float, origin_lat: float, origin_lon: float
) -> tuple[float, float]:
    """Convert local X/Y offsets (metres) to GPS lat/lon using HA home as origin."""
    lat = origin_lat + (posture_y / 111320.0)
    lon = origin_lon + (posture_x / (111320.0 * math.cos(math.radians(origin_lat))))
    return lat, lon


class NavimowCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator for Navimow data updates."""

    def __init__(
        self,
        hass: HomeAssistant,
        sdk: NavimowSDK,
        api: MowerAPI,
        device: Device,
        oauth_session: config_entry_oauth2_flow.OAuth2Session | None = None,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=UPDATE_INTERVAL),
        )
        self.sdk = sdk
        self.api = api
        self.device = device
        self.oauth_session = oauth_session
        self.data: dict[str, Any] = {}
        self._last_state: DeviceStateMessage | None = None
        self._last_attributes: DeviceAttributesMessage | None = None
        self._last_mqtt_update: float | None = None
        self._last_http_fetch: float | None = None
        self._last_data_source: str | None = None
        self._last_location: dict[str, float] | None = None

    async def async_setup(self) -> None:
        """Register callbacks from SDK."""
        self.sdk.on_state(self._handle_state)
        self.sdk.on_attributes(self._handle_attributes)

    def _build_data(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "state": self._last_state,
            "attributes": self._last_attributes,
            "location": self._last_location,
            "meta": {
                "last_data_source": self._last_data_source,
                "last_mqtt_update_monotonic": self._last_mqtt_update,
                "last_http_fetch_monotonic": self._last_http_fetch,
            },
        }

    def _device_status_to_state(self, status: DeviceStatus) -> DeviceStateMessage:
        error: dict[str, Any] | None = None
        if status.error_code and status.error_code.value != "none":
            error = {"code": status.error_code.value, "message": status.error_message}
        return DeviceStateMessage(
            device_id=status.device_id,
            timestamp=status.timestamp,
            state=status.status.value,
            battery=status.battery,
            signal_strength=status.signal_strength,
            position=status.position,
            error=error,
            metrics=None,
        )

    async def _async_ensure_valid_token(self) -> str | None:
        if not self.oauth_session:
            return None
        try:
            token: dict[str, Any] | None
            if hasattr(self.oauth_session, "async_ensure_token_valid"):
                await self.oauth_session.async_ensure_token_valid()
                token = self.oauth_session.token
            elif hasattr(self.oauth_session, "async_get_valid_token"):
                token = await self.oauth_session.async_get_valid_token()
            else:
                token = self.oauth_session.token
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            _LOGGER.warning("Token refresh failed (transient), using cached: %s", err)
            cached = getattr(self.oauth_session, "token", None)
            if cached and cached.get("access_token"):
                token = cached
            else:
                raise ConfigEntryAuthFailed(f"No cached token: {err}") from err
        if not token or not token.get("access_token"):
            raise ConfigEntryAuthFailed("No access token after refresh")
        self.api.set_token(token["access_token"])
        return token["access_token"]

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            await self._async_ensure_valid_token()
        except ConfigEntryAuthFailed:
            raise

        cached_state = self.sdk.get_cached_state(self.device.id)
        if cached_state is not None:
            self._last_state = cached_state
            self._last_data_source = "mqtt_cache"

        cached_attrs = self.sdk.get_cached_attributes(self.device.id)
        if cached_attrs is not None:
            self._last_attributes = cached_attrs

        now = time.monotonic()
        is_mqtt_stale = (
            self._last_mqtt_update is None
            or now - self._last_mqtt_update > MQTT_STALE_SECONDS
        )
        can_http_fetch = (
            self._last_http_fetch is None
            or now - self._last_http_fetch > HTTP_FALLBACK_MIN_INTERVAL
        )
        if is_mqtt_stale and can_http_fetch:
            try:
                status = await self.api.async_get_device_status(self.device.id)
                _LOGGER.debug(
                    "HTTP status: device=%s state=%s battery=%s position=%s",
                    status.device_id,
                    status.status,
                    status.battery,
                    status.position,
                )
                self._last_state = self._device_status_to_state(status)
                self._last_http_fetch = now
                self._last_data_source = "http_fallback"
            except ConfigEntryAuthFailed:
                raise
            except Exception as err:
                _LOGGER.warning("HTTP fallback failed for %s: %s", self.device.id, err)

        _LOGGER.debug(
            "Coordinator update: device=%s source=%s mqtt_ts=%s http_ts=%s",
            self.device.id,
            self._last_data_source,
            self._last_mqtt_update,
            self._last_http_fetch,
        )
        self.data = self._build_data()
        return self.data

    # ------------------------------------------------------------------
    # Raw MQTT hook — called for every channel on this device
    # ------------------------------------------------------------------

    def handle_raw_mqtt(self, topic: str, payload: dict[str, Any], device_id: str) -> None:
        """Receive every raw MQTT message for this device."""
        if device_id != self.device.id:
            return

        _LOGGER.debug(
            "Navimow raw MQTT: device=%s topic=%s payload=%s",
            device_id,
            topic,
            json.dumps(payload, ensure_ascii=False),
        )

        # /realtimeDate/location carries postureX/Y/Theta local coordinates
        if topic.endswith("/realtimeDate/location"):
            location = _extract_local_coords(payload)
            if location:
                origin_lat = self.hass.config.latitude
                origin_lon = self.hass.config.longitude
                lat, lon = _xy_to_latlon(
                    location["posture_x"], location["posture_y"],
                    origin_lat, origin_lon,
                )
                location["lat"] = lat
                location["lng"] = lon
                _LOGGER.debug(
                    "Navimow location: device=%s x=%.3f y=%.3f theta=%.3f → lat=%.7f lng=%.7f",
                    device_id,
                    location["posture_x"], location["posture_y"], location["posture_theta"],
                    lat, lon,
                )
                self.hass.loop.call_soon_threadsafe(self._apply_location, location)
            return

        position = _extract_position(payload)
        if position:
            _LOGGER.debug(
                "Navimow position found: device=%s lat=%s lng=%s topic=%s",
                device_id,
                position.get("lat"),
                position.get("lng"),
                topic,
            )
            self.hass.loop.call_soon_threadsafe(self._apply_position, position)

    def _apply_location(self, location: dict[str, float]) -> None:
        """Store local-coordinate location data and push an update."""
        self._last_location = location
        self.async_set_updated_data(self._build_data())

    def _apply_position(self, position: dict[str, float]) -> None:
        """Apply a newly found position to the current state and push an update."""
        if self._last_state is None:
            self._last_state = DeviceStateMessage(
                device_id=self.device.id,
                timestamp=None,
                state="unknown",
                position=position,
            )
        else:
            self._last_state = DeviceStateMessage(
                device_id=self._last_state.device_id,
                timestamp=self._last_state.timestamp,
                state=self._last_state.state,
                battery=self._last_state.battery,
                signal_strength=self._last_state.signal_strength,
                position=position,
                error=self._last_state.error,
                metrics=self._last_state.metrics,
            )
        self.async_set_updated_data(self._build_data())

    # ------------------------------------------------------------------
    # SDK callbacks
    # ------------------------------------------------------------------

    def _handle_state(self, state: DeviceStateMessage) -> None:
        if state.device_id != self.device.id:
            return
        _LOGGER.debug(
            "MQTT state: device=%s state=%s battery=%s position=%s",
            state.device_id,
            state.state,
            state.battery,
            state.position,
        )
        self._last_mqtt_update = time.monotonic()
        self._last_data_source = "mqtt_push"
        # Preserve position already found via raw hook if SDK didn't parse one.
        if state.position is None and self._last_state and self._last_state.position:
            state = DeviceStateMessage(
                device_id=state.device_id,
                timestamp=state.timestamp,
                state=state.state,
                battery=state.battery,
                signal_strength=state.signal_strength,
                position=self._last_state.position,
                error=state.error,
                metrics=state.metrics,
            )
        self.hass.loop.call_soon_threadsafe(self._update_from_state, state)

    def _handle_attributes(self, attrs: DeviceAttributesMessage) -> None:
        if attrs.device_id != self.device.id:
            return
        self._last_mqtt_update = time.monotonic()
        self.hass.loop.call_soon_threadsafe(self._update_from_attributes, attrs)

    def _update_from_state(self, state: DeviceStateMessage) -> None:
        self._last_state = state
        self._last_data_source = "mqtt_push"
        self.async_set_updated_data(self._build_data())

    def _update_from_attributes(self, attrs: DeviceAttributesMessage) -> None:
        self._last_attributes = attrs
        self.async_set_updated_data(self._build_data())

    def get_device_state(self) -> DeviceStateMessage | None:
        return self.data.get("state")

    def get_device_attributes(self) -> DeviceAttributesMessage | None:
        return self.data.get("attributes")

    def get_device_location(self) -> dict[str, float] | None:
        """Return latest location dict with lat/lng/posture_x/posture_y/posture_theta."""
        return self.data.get("location")

    def get_device_info(self) -> Any | None:
        return self.data.get("device")
