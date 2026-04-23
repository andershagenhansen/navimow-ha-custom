"""Button platform for Navimow integration."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import NavimowCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][config_entry.entry_id]
    async_add_entities(
        NavimowRefreshButton(coordinator=data["coordinators"][device.id])
        for device in data["devices"]
    )


class NavimowRefreshButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "force_refresh"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator: NavimowCoordinator) -> None:
        self._coordinator = coordinator
        device = coordinator.device
        self._attr_unique_id = f"{DOMAIN}_{device.id}_force_refresh"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.id)},
            name=device.name,
            manufacturer="Navimow",
            model=device.model or "Unknown",
            sw_version=device.firmware_version or None,
            serial_number=device.serial_number or device.id,
        )

    async def async_press(self) -> None:
        await self._coordinator.async_request_refresh()
