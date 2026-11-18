"""Set up a Zhuohu meter by LAN scan or by address."""

from ipaddress import IPv4Address

import voluptuous as vol

from homeassistant.components.network import async_get_adapters
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.helpers.device_registry import format_mac
from homeassistant.helpers.selector import (
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import CONF_KIND, CONF_MAC, DOMAIN
from .discovery import DiscoveredMeter, async_probe, async_scan, lan_ip
from .profiles import PROFILES
from .runtime import SESSION_ERRORS, async_validate


class ZhuohuLocalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure one meter per entry."""

    VERSION = 1

    def __init__(self):
        self._found = None
        self._manual_host = None

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["scan", "manual"])

    async def async_step_scan(self, user_input=None) -> ConfigFlowResult:
        if self._found is None:
            configured = {entry.unique_id for entry in self._async_current_entries()}
            devices = await async_scan(await async_get_adapters(self.hass))
            self._found = {meter.mac: meter for meter in devices if meter.mac not in configured}
        if not self._found:
            return self.async_abort(reason="no_devices")

        errors = {}
        if user_input is not None:
            meter = self._found.get(user_input["meter"])
            if meter is not None:
                try:
                    await async_validate(meter.host, PROFILES[meter.kind])
                except SESSION_ERRORS:
                    errors["base"] = "cannot_connect"
                else:
                    return await self._create_meter(meter)
            else:
                errors["base"] = "invalid_device"
        choices = [
            SelectOptionDict(
                value=meter.mac,
                label=f"{meter.host} · {PROFILES[meter.kind].model} · {meter.mac}",
            )
            for meter in self._found.values()
        ]
        return self.async_show_form(
            step_id="scan",
            data_schema=vol.Schema({
                vol.Required("meter"): SelectSelector(
                    SelectSelectorConfig(options=choices, mode=SelectSelectorMode.DROPDOWN)
                )
            }),
            errors=errors,
        )

    async def async_step_manual(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            try:
                host = lan_ip(user_input[CONF_HOST].strip())
            except ValueError:
                errors[CONF_HOST] = "invalid_ip"
            else:
                try:
                    meter = await async_probe(host)
                except OSError:
                    errors["base"] = "cannot_connect"
                else:
                    if meter is None:
                        self._manual_host = host
                        return await self.async_step_manual_details()
                    try:
                        await async_validate(meter.host, PROFILES[meter.kind])
                    except SESSION_ERRORS:
                        errors["base"] = "cannot_connect"
                    else:
                        return await self._create_meter(meter)
        return self.async_show_form(
            step_id="manual",
            data_schema=vol.Schema({vol.Required(CONF_HOST): str}),
            errors=errors,
        )

    async def async_step_manual_details(self, user_input=None) -> ConfigFlowResult:
        """Accept explicit identity if UDP discovery is unavailable."""
        errors = {}
        host = self._manual_host
        if host is None:
            return await self.async_step_manual()
        if user_input is not None:
            mac = format_mac(user_input[CONF_MAC])
            try:
                octets = bytes.fromhex(mac.replace(":", ""))
                if len(octets) != 6 or not any(octets) or octets[0] & 1:
                    raise ValueError("Invalid hardware address")
            except ValueError:
                errors[CONF_MAC] = "invalid_mac"
            else:
                kind = user_input[CONF_KIND]
                try:
                    await async_validate(host, PROFILES[kind])
                except SESSION_ERRORS:
                    errors["base"] = "cannot_connect"
                else:
                    return await self._create_meter(DiscoveredMeter(host, octets.hex(":"), kind))
        models = [SelectOptionDict(value=kind, label=profile.model) for kind, profile in PROFILES.items()]
        return self.async_show_form(
            step_id="manual_details",
            data_schema=vol.Schema({
                vol.Required(CONF_MAC): str,
                vol.Required(CONF_KIND): SelectSelector(SelectSelectorConfig(options=models)),
            }),
            description_placeholders={"host": host},
            errors=errors,
        )

    async def async_step_import(self, user_input) -> ConfigFlowResult:
        try:
            host = lan_ip(user_input[CONF_HOST])
            if any(entry.data[CONF_HOST] == host for entry in self._async_current_entries()):
                return self.async_abort(reason="already_configured")
            meter = await async_probe(host)
            if meter is None:
                return self.async_abort(reason="unsupported_device")
            await async_validate(meter.host, PROFILES[meter.kind])
        except SESSION_ERRORS:
            return self.async_abort(reason="cannot_connect")
        return await self._create_meter(meter)

    async def _create_meter(self, meter):
        await self.async_set_unique_id(meter.mac)
        self._abort_if_unique_id_configured(updates={CONF_HOST: meter.host})
        return self.async_create_entry(
            title=f"卓虎电表 {IPv4Address(meter.host)}",
            data={CONF_HOST: meter.host, CONF_MAC: meter.mac, CONF_KIND: meter.kind},
        )
