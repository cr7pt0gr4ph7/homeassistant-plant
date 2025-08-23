"""Support for monitoring plants."""

from __future__ import annotations

import logging

import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import (
    Platform,
    ATTR_NAME,
)
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.entity_component import EntityComponent

from .const import (
    ATTR_COMPONENT,
    ATTR_PLANT,
    ATTR_SENSORS,
    DOMAIN,
    DOMAIN_PLANTBOOK,
    FLOW_PLANT_INFO,
    SERVICE_REPLACE_SENSOR,
    USE_DUMMY_SENSORS,
)
from .plant import PlantDevice
from .plant_helpers import PlantHelper

_LOGGER = logging.getLogger(__name__)
PLATFORMS = [Platform.NUMBER, Platform.SENSOR]

# Removed.
# Have not been used for a long time
#
# async def async_setup(hass: HomeAssistant, config: dict):
#     """
#     Set up the plant component
#
#     Configuration.yaml is no longer used.
#     This function only tries to migrate the legacy config.
#     """
#     if config.get(DOMAIN):
#         # Only import if we haven't before.
#         config_entry = _async_find_matching_config_entry(hass)
#         if not config_entry:
#             _LOGGER.debug("Old setup - with config: %s", config[DOMAIN])
#             for plant in config[DOMAIN]:
#                 if plant != DOMAIN_PLANTBOOK:
#                     _LOGGER.info("Migrating plant: %s", plant)
#                     await async_migrate_plant(hass, plant, config[DOMAIN][plant])
#         else:
#             _LOGGER.warning(
#                 "Config already imported. Please delete all your %s related config from configuration.yaml",
#                 DOMAIN,
#             )
#     return True


@callback
def _async_find_matching_config_entry(hass: HomeAssistant) -> ConfigEntry | None:
    """Check if there are migrated entities"""
    for entry in hass.config_entries.async_entries(DOMAIN):
        if entry.source == SOURCE_IMPORT:
            return entry


async def async_migrate_plant(hass: HomeAssistant, plant_id: str, config: dict) -> None:
    """Try to migrate the config from yaml"""

    if ATTR_NAME not in config:
        config[ATTR_NAME] = plant_id.replace("_", " ").capitalize()
    plant_helper = PlantHelper(hass)
    plant_config = await plant_helper.generate_configentry(config=config)
    hass.async_create_task(
        hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data=plant_config
        )
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Set up Plant from a config entry."""
    hass.data.setdefault(DOMAIN, {})
    if FLOW_PLANT_INFO not in entry.data:
        return True

    hass.data[DOMAIN].setdefault(entry.entry_id, {})
    _LOGGER.debug("Setting up config entry %s: %s", entry.entry_id, entry)

    # Add all the entities to Hass
    component = EntityComponent(_LOGGER, DOMAIN, hass)
    hass.data[DOMAIN][entry.entry_id][ATTR_COMPONENT] = component
    await component.async_setup_entry(entry)
    plant = hass.data[DOMAIN][entry.entry_id][ATTR_PLANT]

    # Add the rest of the entities to device registry together with plant
    device_id = plant.device_id
    await _plant_add_to_device_registry(hass, plant_entities, device_id)
    # await _plant_add_to_device_registry(hass, plant.integral_entities, device_id)
    # await _plant_add_to_device_registry(hass, plant.threshold_entities, device_id)
    # await _plant_add_to_device_registry(hass, plant.meter_entities, device_id)

    # Create the sensor and number entities
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    #
    # Register replace_sensor service and websocket handler
    await _async_register_services_and_ws_handler(hass)
    plant.async_schedule_update_ha_state(True)

    # Lets add the dummy sensors automatically if we are testing stuff
    if USE_DUMMY_SENSORS is True:
        for sensor in plant.meter_entities:
            if sensor.external_sensor is None:
                await hass.services.async_call(
                    domain=DOMAIN,
                    service=SERVICE_REPLACE_SENSOR,
                    service_data={
                        "meter_entity": sensor.entity_id,
                        "new_sensor": sensor.entity_id.replace(
                            "sensor.", "sensor.dummy_"
                        ),
                    },
                    blocking=False,
                    limit=30,
                )

    return True


async def _plant_add_to_device_registry(
    hass: HomeAssistant, plant_entities: list[Entity], device_id: str
) -> None:
    """Add all related entities to the correct device_id"""

    # There must be a better way to do this, but I just can't find a way to set the
    # device_id when adding the entities.
    erreg = er.async_get(hass)
    for entity in plant_entities:
        erreg.async_update_entity(entity.registry_entry.entity_id, device_id=device_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    # Unload the Plant entity
    component = hass.data[DOMAIN][entry.entry_id][ATTR_COMPONENT]
    unload_ok = not component is None and await component.async_unload_entry(entry)

    # Unload all other entities
    if unload_ok:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        _LOGGER.info(hass.data[DOMAIN])
        for entry_id in list(hass.data[DOMAIN].keys()):
            if len(hass.data[DOMAIN][entry_id]) == 0:
                _LOGGER.info("Removing entry %s", entry_id)
                del hass.data[DOMAIN][entry_id]
        if len(hass.data[DOMAIN]) == 0:
            _LOGGER.info("Removing domain %s", DOMAIN)
            hass.services.async_remove(DOMAIN, SERVICE_REPLACE_SENSOR)
            del hass.data[DOMAIN]

    return unload_ok


async def _async_register_services_and_ws_handler(
    hass: HomeAssistant
) -> None:
    """Register services and websocket handlers."""

    #
    # Service call to replace sensors
    async def replace_sensor(call: ServiceCall) -> None:
        """Replace a sensor entity within a plant device"""
        meter_entity = call.data.get("meter_entity")
        new_sensor = call.data.get("new_sensor")
        found = False
        for entry_id in hass.data[DOMAIN]:
            if ATTR_SENSORS in hass.data[DOMAIN][entry_id]:
                for sensor in hass.data[DOMAIN][entry_id][ATTR_SENSORS]:
                    if sensor.entity_id == meter_entity:
                        found = True
                        break
        if not found:
            _LOGGER.warning(
                "Refuse to update non-%s entities: %s", DOMAIN, meter_entity
            )
            return False
        if new_sensor and new_sensor != "" and not new_sensor.startswith("sensor."):
            _LOGGER.warning("%s is not a sensor", new_sensor)
            return False

        try:
            meter = hass.states.get(meter_entity)
        except AttributeError:
            _LOGGER.error("Meter entity %s not found", meter_entity)
            return False
        if meter is None:
            _LOGGER.error("Meter entity %s not found", meter_entity)
            return False

        if new_sensor and new_sensor != "":
            try:
                test = hass.states.get(new_sensor)
            except AttributeError:
                _LOGGER.error("New sensor entity %s not found", meter_entity)
                return False
            if test is None:
                _LOGGER.error("New sensor entity %s not found", meter_entity)
                return False
        else:
            new_sensor = None

        _LOGGER.info(
            "Going to replace the external sensor for %s with %s",
            meter_entity,
            new_sensor,
        )
        for key in hass.data[DOMAIN]:
            if ATTR_SENSORS in hass.data[DOMAIN][key]:
                meters = hass.data[DOMAIN][key][ATTR_SENSORS]
                for meter in meters:
                    if meter.entity_id == meter_entity:
                        meter.replace_external_sensor(new_sensor)
        return

    hass.services.async_register(DOMAIN, SERVICE_REPLACE_SENSOR, replace_sensor)
    websocket_api.async_register_command(hass, ws_get_info)


@websocket_api.websocket_command(
    {
        vol.Required("type"): "plant/get_info",
        vol.Required("entity_id"): str,
    }
)
@callback
def ws_get_info(
    hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict
) -> None:
    """Handle the websocket command."""
    # _LOGGER.debug("Got websocket request: %s", msg)

    if DOMAIN not in hass.data:
        connection.send_error(
            msg["id"], "domain_not_found", f"Domain {DOMAIN} not found"
        )
        return

    for key in hass.data[DOMAIN]:
        if not ATTR_PLANT in hass.data[DOMAIN][key]:
            continue
        plant_entity = hass.data[DOMAIN][key][ATTR_PLANT]
        if plant_entity.entity_id == msg["entity_id"]:
            # _LOGGER.debug("Sending websocket response: %s", plant_entity.websocket_info)
            try:
                connection.send_result(
                    msg["id"], {"result": plant_entity.websocket_info}
                )
            except ValueError as e:
                _LOGGER.warning(e)
            return
    connection.send_error(
        msg["id"], "entity_not_found", f"Entity {msg['entity_id']} not found"
    )
    return
