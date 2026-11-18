"""Zhuohu meters connected directly to Home Assistant."""

import voluptuous as vol

from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_HOST, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from .const import CONF_HOSTS, CONF_KIND, DOMAIN
from .profiles import PROFILES
from .runtime import MeterRuntime

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = vol.Schema(
    {vol.Optional(DOMAIN): {vol.Required(CONF_HOSTS): vol.All(cv.ensure_list, [cv.string])}},
    extra=vol.ALLOW_EXTRA,
)

type MeterConfigEntry = ConfigEntry[MeterRuntime]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Import known addresses when migrating an existing deployment."""
    for host in config.get(DOMAIN, {}).get(CONF_HOSTS, []):
        hass.async_create_task(
            hass.config_entries.flow.async_init(
                DOMAIN, context={"source": SOURCE_IMPORT}, data={CONF_HOST: host}
            ),
            f"{DOMAIN} import {host}",
        )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: MeterConfigEntry) -> bool:
    """Add the meter and start its tracked connection task."""
    meter = MeterRuntime(entry.data[CONF_HOST], PROFILES[entry.data[CONF_KIND]])
    entry.runtime_data = meter
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_create_background_task(hass, meter.run(), f"{DOMAIN} {entry.entry_id}")
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MeterConfigEntry) -> bool:
    """Unload the sensors and let Home Assistant cancel the connection task."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
