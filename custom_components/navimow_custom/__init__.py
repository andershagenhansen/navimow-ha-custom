"""The Navimow integration."""
from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import urlparse

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_entry_oauth2_flow
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .auth import NavimowOAuth2Implementation
from .const import (
    API_BASE_URL,
    CLIENT_ID,
    CLIENT_SECRET,
    DOMAIN,
    MQTT_BROKER,
    MQTT_PASSWORD,
    MQTT_PORT,
    MQTT_USERNAME,
)
from .services import async_setup_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BUTTON,
    Platform.LAWN_MOWER,
    Platform.SENSOR,
    Platform.DEVICE_TRACKER,
]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    """Set up the Navimow component."""
    hass.data.setdefault(DOMAIN, {})
    config_entry_oauth2_flow.async_register_implementation(
        hass,
        DOMAIN,
        NavimowOAuth2Implementation(hass, DOMAIN, CLIENT_ID, CLIENT_SECRET),
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up Navimow from a config entry."""
    from mower_sdk.api import MowerAPI
    from mower_sdk.errors import MowerAPIError
    from mower_sdk.sdk import NavimowSDK

    from .coordinator import NavimowCoordinator

    hass.data.setdefault(DOMAIN, {})

    def _mask(value: str | None) -> str:
        if not value:
            return "<empty>"
        if len(value) <= 4:
            return "*" * len(value)
        return f"{value[:2]}***{value[-2:]}"

    try:
        implementation = await config_entry_oauth2_flow.async_get_config_entry_implementation(
            hass, entry
        )
        if not isinstance(implementation, NavimowOAuth2Implementation):
            raise ConfigEntryAuthFailed("Invalid OAuth2 implementation")

        oauth_session = config_entry_oauth2_flow.OAuth2Session(hass, entry, implementation)

        token: dict[str, Any] | None = None
        if hasattr(oauth_session, "async_get_valid_token"):
            try:
                token = await oauth_session.async_get_valid_token()
            except AttributeError:
                token = None
        if not token and hasattr(oauth_session, "async_ensure_token_valid"):
            await oauth_session.async_ensure_token_valid()
            token = oauth_session.token
        if not token:
            token = entry.data.get("token")
        if not token or not token.get("access_token"):
            raise ConfigEntryAuthFailed("No valid token available")

        access_token = token["access_token"]
        api = MowerAPI(
            session=async_get_clientsession(hass),
            token=access_token,
            base_url=entry.data.get("api_base_url", API_BASE_URL),
        )

        try:
            devices = await api.async_get_devices()
            _LOGGER.info("Discovered %d Navimow device(s)", len(devices))
        except MowerAPIError as err:
            raise ConfigEntryNotReady(f"Failed to discover devices: {err}") from err
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err

        try:
            mqtt_info = await api.async_get_mqtt_user_info()
        except MowerAPIError as err:
            raise ConfigEntryNotReady(f"Failed to get MQTT info: {err}") from err

        mqtt_host = mqtt_info.get("mqttHost") or entry.data.get("mqtt_broker", MQTT_BROKER)
        mqtt_url = mqtt_info.get("mqttUrl")
        mqtt_username = mqtt_info.get("userName") or entry.data.get("mqtt_username", MQTT_USERNAME)
        mqtt_password = mqtt_info.get("pwdInfo") or entry.data.get("mqtt_password", MQTT_PASSWORD)
        mqtt_port = 443 if mqtt_url else entry.data.get("mqtt_port", MQTT_PORT)
        ws_path = mqtt_url
        if mqtt_url:
            parsed = urlparse(mqtt_url)
            if parsed.scheme in ("ws", "wss") and parsed.hostname:
                mqtt_host = mqtt_host or parsed.hostname
                if parsed.port:
                    mqtt_port = parsed.port
                ws_path = parsed.path or "/"
                if parsed.query:
                    ws_path = f"{ws_path}?{parsed.query}"
        auth_headers = {"Authorization": f"Bearer {access_token}"} if ws_path else None

        _LOGGER.info(
            "MQTT connection: broker=%s port=%s username=%s",
            mqtt_host, mqtt_port, _mask(mqtt_username),
        )

        _mqtt_refresh_lock = asyncio.Lock()
        _unload_flag: list[bool] = [False]

        def _attach_mqtt_hooks(
            sdk: NavimowSDK,
            api: MowerAPI,
            get_coordinators=None,
        ) -> None:
            mqtt = sdk._mqtt
            original_on_message = mqtt.on_message

            def _client_id() -> str:
                cid = getattr(mqtt.client, "_client_id", b"")
                if isinstance(cid, (bytes, bytearray)):
                    return cid.decode("utf-8", errors="replace") or "<empty>"
                return str(cid) if cid else "<empty>"

            async def _on_connected() -> None:
                _LOGGER.info(
                    "MQTT connected: broker=%s port=%s client_id=%s",
                    mqtt.broker, mqtt.port, _client_id(),
                )

            async def _on_ready() -> None:
                _LOGGER.info(
                    "MQTT ready: broker=%s port=%s client_id=%s",
                    mqtt.broker, mqtt.port, _client_id(),
                )
                device_ids = [getattr(d, "id", None) for d in mqtt.records if d]
                for device_id in device_ids:
                    for channel in ("location", "state", "event", "attributes"):
                        topic = f"/downlink/vehicle/{device_id}/realtimeDate/{channel}"
                        rc, mid = mqtt.client.subscribe(topic)
                        _LOGGER.info("Navimow: subscribed %s (rc=%s mid=%s)", topic, rc, mid)

            async def _on_disconnected() -> None:
                if _unload_flag[0]:
                    return
                if _mqtt_refresh_lock.locked():
                    return
                async with _mqtt_refresh_lock:
                    if _unload_flag[0]:
                        return
                    await _async_refresh_mqtt_credentials(sdk, api)

            async def _on_message(topic: str, payload: bytes, device_id: str) -> None:
                payload_text = (payload or b"").decode("utf-8", errors="replace")
                _LOGGER.debug(
                    "MQTT message: topic=%s device=%s payload=%s",
                    topic, device_id, payload_text,
                )
                if get_coordinators is not None:
                    try:
                        import json as _json
                        parsed = _json.loads(payload_text)
                        # location channel sends a JSON array; unwrap first element
                        if isinstance(parsed, list):
                            payload_dict = parsed[0] if parsed and isinstance(parsed[0], dict) else None
                        elif isinstance(parsed, dict):
                            payload_dict = parsed
                        else:
                            payload_dict = None
                        if payload_dict is not None:
                            payload_dict.setdefault("device_id", device_id)
                            for coord in get_coordinators().values():
                                coord.handle_raw_mqtt(topic, payload_dict, device_id)
                    except Exception:
                        pass
                if original_on_message is not None:
                    await original_on_message(topic, payload, device_id)

            mqtt.on_connected = _on_connected
            mqtt.on_ready = _on_ready
            mqtt.on_disconnected = _on_disconnected
            mqtt.on_message = _on_message

            # If MQTT already connected before hooks were installed, subscribe now
            if mqtt.is_connected:
                hass.async_create_task(_on_ready())

            def _on_subscribe(_client, _userdata, mid, granted_qos, *args, **kwargs):
                _LOGGER.debug(
                    "MQTT subscribed: mid=%s granted_qos=%s", mid, granted_qos
                )

            def _on_log(_client, _userdata, level, buf):
                _LOGGER.debug("MQTT client log: level=%s msg=%s", level, buf)

            mqtt.client.on_subscribe = _on_subscribe
            mqtt.client.on_log = _on_log

        async def _async_refresh_mqtt_credentials(sdk: NavimowSDK, api: MowerAPI) -> None:
            new_access_token: str | None = None
            new_auth_headers: dict[str, str] | None = None
            try:
                if hasattr(oauth_session, "async_ensure_token_valid"):
                    await oauth_session.async_ensure_token_valid()
                    fresh_token = oauth_session.token
                elif hasattr(oauth_session, "async_get_valid_token"):
                    fresh_token = await oauth_session.async_get_valid_token()
                else:
                    fresh_token = oauth_session.token
                if fresh_token and fresh_token.get("access_token"):
                    new_access_token = fresh_token["access_token"]
                    api.set_token(new_access_token)
                    new_auth_headers = {"Authorization": f"Bearer {new_access_token}"}
            except Exception as err:
                _LOGGER.warning("OAuth token refresh failed: %s", err)

            try:
                new_mqtt_info = await api.async_get_mqtt_user_info()
            except Exception as err:
                _LOGGER.warning("MQTT credential refresh failed: %s", err)
                return
            new_username = new_mqtt_info.get("userName")
            new_password = new_mqtt_info.get("pwdInfo")
            if new_auth_headers or new_username or new_password:
                _new_auth_headers = new_auth_headers
                _new_username = new_username
                _new_password = new_password

                def _do_update() -> None:
                    sdk.update_mqtt_credentials(
                        auth_headers=_new_auth_headers,
                        username=_new_username,
                        password=_new_password,
                    )

                await hass.async_add_executor_job(_do_update)
                _LOGGER.info(
                    "MQTT credentials refreshed: username=%s", _mask(new_username)
                )

        async def _probe_mqtt_status(sdk: NavimowSDK) -> None:
            await asyncio.sleep(5)
            _LOGGER.info("MQTT status probe (5s): connected=%s", sdk.is_connected)
            await asyncio.sleep(25)
            _LOGGER.info("MQTT status probe (30s): connected=%s", sdk.is_connected)

        def _create_sdk(api: MowerAPI) -> NavimowSDK:
            sdk = NavimowSDK(
                broker=mqtt_host,
                port=mqtt_port,
                username=mqtt_username,
                password=mqtt_password,
                ws_path=ws_path,
                auth_headers=auth_headers,
                loop=hass.loop,
                records=devices,
                keepalive_seconds=2400,
                reconnect_min_delay=1,
                reconnect_max_delay=60,
            )
            sdk.connect()
            return sdk

        sdk = await hass.async_add_executor_job(_create_sdk, api)
        async_setup_services(hass, api)
        hass.async_create_task(_probe_mqtt_status(sdk))

        coordinators: dict[str, NavimowCoordinator] = {}
        for device in devices:
            coordinator = NavimowCoordinator(
                hass=hass,
                sdk=sdk,
                api=api,
                device=device,
                oauth_session=oauth_session,
            )
            await coordinator.async_setup()
            await coordinator.async_config_entry_first_refresh()
            coordinators[device.id] = coordinator

        _attach_mqtt_hooks(sdk, api, get_coordinators=lambda: coordinators)

        hass.data[DOMAIN][entry.entry_id] = {
            "sdk": sdk,
            "api": api,
            "devices": devices,
            "coordinators": coordinators,
            "oauth_session": oauth_session,
            "unload_flag": _unload_flag,
        }

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        return True

    except ConfigEntryAuthFailed:
        raise
    except Exception as err:
        _LOGGER.exception("Error setting up Navimow: %s", err)
        raise ConfigEntryNotReady(f"Setup error: {err}") from err


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok and entry.entry_id in hass.data.get(DOMAIN, {}):
        data = hass.data[DOMAIN].pop(entry.entry_id)
        if "unload_flag" in data:
            data["unload_flag"][0] = True
        sdk = data.get("sdk")
        if sdk:
            try:
                sdk.disconnect()
            except Exception as err:
                _LOGGER.warning("Error disconnecting MQTT: %s", err)
    return unload_ok
