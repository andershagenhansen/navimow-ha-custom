"""Device tracker platform for Navimow integration."""
from __future__ import annotations

from homeassistant.components.device_tracker import SourceType
from homeassistant.components.device_tracker.config_entry import TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import NavimowCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Navimow device tracker from a config entry."""
    data = hass.data[DOMAIN][config_entry.entry_id]
    coordinators: dict[str, NavimowCoordinator] = data["coordinators"]
    async_add_entities(
        NavimowDeviceTracker(coordinator=coordinators[device.id])
        for device in data["devices"]
    )


class NavimowDeviceTracker(CoordinatorEntity[NavimowCoordinator], TrackerEntity):
    """Tracks the GPS position of a Navimow mower."""

    _attr_has_entity_name = True
    _attr_translation_key = "mower_location"

    def __init__(self, coordinator: NavimowCoordinator) -> None:
        super().__init__(coordinator)
        device = coordinator.device
        self._attr_unique_id = f"{DOMAIN}_{device.id}_location"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device.id)},
            name=device.name,
            manufacturer="Navimow",
            model=device.model or "Unknown",
            sw_version=device.firmware_version or None,
            serial_number=device.serial_number or device.id,
        )

    @property
    def source_type(self) -> SourceType:
        return SourceType.GPS

    @property
    def latitude(self) -> float | None:
        state = self.coordinator.get_device_state()
        if state and state.position:
            return state.position.get("lat")
        return None

    @property
    def longitude(self) -> float | None:
        state = self.coordinator.get_device_state()
        if state and state.position:
            return state.position.get("lng")
        return None

    @property
    def available(self) -> bool:
        return self.coordinator.get_device_state() is not None or super().available
