"""Native Home Assistant measurement and energy sensors."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import MeterConfigEntry
from .const import CONF_MAC, DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: MeterConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    meter = entry.runtime_data
    async_add_entities(ZhuohuSensor(entry, sensor) for sensor in meter.profile.sensors.values())


class ZhuohuSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry, sensor):
        self.meter = entry.runtime_data
        self.key = sensor.key
        mac = entry.data[CONF_MAC]
        self._attr_name = sensor.name
        self._attr_unique_id = f"{mac.replace(':', '')}_{sensor.key}"
        self._attr_suggested_object_id = f"zhuohu_meter_{self.meter.host.rsplit('.', 1)[1]}_{sensor.key}"
        self._attr_device_class = SensorDeviceClass(sensor.device_class)
        self._attr_state_class = SensorStateClass(sensor.state_class)
        self._attr_native_unit_of_measurement = sensor.unit or None
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, mac)},
            connections={(CONNECTION_NETWORK_MAC, mac)},
            name=entry.title,
            manufacturer="卓虎",
            model=self.meter.profile.model,
        )

    @property
    def available(self):
        return self.meter.available and self.key in self.meter.values

    @property
    def native_value(self):
        return self.meter.values.get(self.key)

    async def async_added_to_hass(self):
        self.async_on_remove(self.meter.listen(self._updated))

    @callback
    def _updated(self, keys):
        if keys is None or self.key in keys:
            self.async_write_ha_state()
