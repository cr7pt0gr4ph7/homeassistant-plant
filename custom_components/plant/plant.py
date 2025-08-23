"""Support for monitoring plants."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_ENTITY_PICTURE,
    ATTR_ICON,
    ATTR_NAME,
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_OK,
    STATE_PROBLEM,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity import Entity, async_generate_entity_id

from .const import (
    ATTR_CONDUCTIVITY,
    ATTR_CURRENT,
    ATTR_DLI,
    ATTR_HUMIDITY,
    ATTR_ILLUMINANCE,
    ATTR_LIMITS,
    ATTR_MAX,
    ATTR_METERS,
    ATTR_MIN,
    ATTR_MOISTURE,
    ATTR_PLANT,
    ATTR_SENSOR,
    ATTR_SENSORS,
    ATTR_SPECIES,
    ATTR_TEMPERATURE,
    ATTR_THRESHOLDS,
    DATA_SOURCE,
    DOMAIN,
    FLOW_CONDUCTIVITY_TRIGGER,
    FLOW_DLI_TRIGGER,
    FLOW_HUMIDITY_TRIGGER,
    FLOW_ILLUMINANCE_TRIGGER,
    FLOW_MOISTURE_TRIGGER,
    FLOW_PLANT_INFO,
    FLOW_TEMPERATURE_TRIGGER,
    OPB_DISPLAY_PID,
    STATE_HIGH,
    STATE_LOW,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
):
    """Set up Plant from a config entry."""
    plant = PlantDevice(hass, entry)

    # Store as runtime data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN].setdefault(entry.entry_id, {})
    hass.data[DOMAIN][entry.entry_id][ATTR_PLANT] = plant
    entry.runtime_data = plant

    # Add Plant entity to Hass
    async_add_entities([plant])


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    return True


class PlantDevice(Entity):
    """Base device for plants"""

    def __init__(self, hass: HomeAssistant, config: ConfigEntry) -> None:
        """Initialize the Plant component."""
        self._config = config
        self._hass = hass
        self._attr_name = config.data[FLOW_PLANT_INFO][ATTR_NAME]
        self._config_entries = []
        self._data_source = config.data[FLOW_PLANT_INFO].get(DATA_SOURCE)

        # Get entity_picture from options or from initial config
        self._attr_entity_picture = self._config.options.get(
            ATTR_ENTITY_PICTURE,
            self._config.data[FLOW_PLANT_INFO].get(ATTR_ENTITY_PICTURE),
        )
        # Get species from options or from initial config
        self.species = self._config.options.get(
            ATTR_SPECIES, self._config.data[FLOW_PLANT_INFO].get(ATTR_SPECIES)
        )
        # Get display_species from options or from initial config
        self.display_species = (
            self._config.options.get(
                OPB_DISPLAY_PID, self._config.data[FLOW_PLANT_INFO].get(OPB_DISPLAY_PID)
            )
            or self.species
        )
        self._attr_unique_id = self._config.entry_id

        self.entity_id = async_generate_entity_id(
            f"{DOMAIN}.{{}}", self.name, current_ids={}
        )

        self.plant_complete = False
        self._device_id = None

        self._check_days = None

        self.max_moisture = None
        self.min_moisture = None
        self.max_temperature = None
        self.min_temperature = None
        self.max_conductivity = None
        self.min_conductivity = None
        self.max_illuminance = None
        self.min_illuminance = None
        self.max_humidity = None
        self.min_humidity = None
        self.max_dli = None
        self.min_dli = None

        self.sensor_moisture = None
        self.sensor_temperature = None
        self.sensor_conductivity = None
        self.sensor_illuminance = None
        self.sensor_humidity = None

        self.dli = None
        self.micro_dli = None
        self.ppfd = None
        self.total_integral = None

        self.conductivity_status = None
        self.illuminance_status = None
        self.moisture_status = None
        self.temperature_status = None
        self.humidity_status = None
        self.dli_status = None

    @property
    def entity_category(self) -> None:
        """The plant device itself does not have a category"""
        return None

    @property
    def device_class(self):
        return DOMAIN

    @property
    def device_id(self) -> str:
        """The device ID used for all the entities"""
        return self._device_id

    @property
    def device_info(self) -> dict:
        """Device info for devices"""
        return {
            "identifiers": {(DOMAIN, self.unique_id)},
            "name": self.name,
            "config_entries": self._config_entries,
            "model": self.display_species,
            "manufacturer": self.data_source,
        }

    @property
    def illuminance_trigger(self) -> bool:
        """Whether we will generate alarms based on illuminance"""
        return self._config.options.get(FLOW_ILLUMINANCE_TRIGGER, True)

    @property
    def humidity_trigger(self) -> bool:
        """Whether we will generate alarms based on humidity"""
        return self._config.options.get(FLOW_HUMIDITY_TRIGGER, True)

    @property
    def temperature_trigger(self) -> bool:
        """Whether we will generate alarms based on temperature"""
        return self._config.options.get(FLOW_TEMPERATURE_TRIGGER, True)

    @property
    def dli_trigger(self) -> bool:
        """Whether we will generate alarms based on dli"""
        return self._config.options.get(FLOW_DLI_TRIGGER, True)

    @property
    def moisture_trigger(self) -> bool:
        """Whether we will generate alarms based on moisture"""
        return self._config.options.get(FLOW_MOISTURE_TRIGGER, True)

    @property
    def conductivity_trigger(self) -> bool:
        """Whether we will generate alarms based on conductivity"""
        return self._config.options.get(FLOW_CONDUCTIVITY_TRIGGER, True)

    @property
    def extra_state_attributes(self) -> dict:
        """Return the device specific state attributes."""
        if not self.plant_complete:
            # We are not fully set up, so we just return an empty dict for now
            return {}
        attributes = {
            ATTR_SPECIES: self.display_species,
            f"{ATTR_MOISTURE}_status": self.moisture_status,
            f"{ATTR_TEMPERATURE}_status": self.temperature_status,
            f"{ATTR_CONDUCTIVITY}_status": self.conductivity_status,
            f"{ATTR_ILLUMINANCE}_status": self.illuminance_status,
            f"{ATTR_HUMIDITY}_status": self.humidity_status,
            f"{ATTR_DLI}_status": self.dli_status,
            f"{ATTR_SPECIES}_original": self.species,
        }
        return attributes

    @property
    def websocket_info(self) -> dict:
        """Wesocket response"""
        if not self.plant_complete:
            # We are not fully set up, so we just return an empty dict for now
            return {}

        response = {
            ATTR_TEMPERATURE: {
                ATTR_MAX: self.max_temperature.state,
                ATTR_MIN: self.min_temperature.state,
                ATTR_CURRENT: self.sensor_temperature.state or STATE_UNAVAILABLE,
                ATTR_ICON: self.sensor_temperature.icon,
                ATTR_UNIT_OF_MEASUREMENT: self.sensor_temperature.unit_of_measurement,
                ATTR_SENSOR: self.sensor_temperature.entity_id,
            },
            ATTR_ILLUMINANCE: {
                ATTR_MAX: self.max_illuminance.state,
                ATTR_MIN: self.min_illuminance.state,
                ATTR_CURRENT: self.sensor_illuminance.state or STATE_UNAVAILABLE,
                ATTR_ICON: self.sensor_illuminance.icon,
                ATTR_UNIT_OF_MEASUREMENT: self.sensor_illuminance.unit_of_measurement,
                ATTR_SENSOR: self.sensor_illuminance.entity_id,
            },
            ATTR_MOISTURE: {
                ATTR_MAX: self.max_moisture.state,
                ATTR_MIN: self.min_moisture.state,
                ATTR_CURRENT: self.sensor_moisture.state or STATE_UNAVAILABLE,
                ATTR_ICON: self.sensor_moisture.icon,
                ATTR_UNIT_OF_MEASUREMENT: self.sensor_moisture.unit_of_measurement,
                ATTR_SENSOR: self.sensor_moisture.entity_id,
            },
            ATTR_CONDUCTIVITY: {
                ATTR_MAX: self.max_conductivity.state,
                ATTR_MIN: self.min_conductivity.state,
                ATTR_CURRENT: self.sensor_conductivity.state or STATE_UNAVAILABLE,
                ATTR_ICON: self.sensor_conductivity.icon,
                ATTR_UNIT_OF_MEASUREMENT: self.sensor_conductivity.unit_of_measurement,
                ATTR_SENSOR: self.sensor_conductivity.entity_id,
            },
            ATTR_HUMIDITY: {
                ATTR_MAX: self.max_humidity.state,
                ATTR_MIN: self.min_humidity.state,
                ATTR_CURRENT: self.sensor_humidity.state or STATE_UNAVAILABLE,
                ATTR_ICON: self.sensor_humidity.icon,
                ATTR_UNIT_OF_MEASUREMENT: self.sensor_humidity.unit_of_measurement,
                ATTR_SENSOR: self.sensor_humidity.entity_id,
            },
            ATTR_DLI: {
                ATTR_MAX: self.max_dli.state,
                ATTR_MIN: self.min_dli.state,
                ATTR_CURRENT: STATE_UNAVAILABLE,
                ATTR_ICON: self.dli.icon,
                ATTR_UNIT_OF_MEASUREMENT: self.dli.unit_of_measurement,
                ATTR_SENSOR: self.dli.entity_id,
            },
        }
        if self.dli.state and self.dli.state != STATE_UNKNOWN:
            response[ATTR_DLI][ATTR_CURRENT] = float(self.dli.state)

        return response

    @property
    def threshold_entities(self) -> list[Entity]:
        """List all threshold entities"""
        return [
            self.max_conductivity,
            self.max_dli,
            self.max_humidity,
            self.max_illuminance,
            self.max_moisture,
            self.max_temperature,
            self.min_conductivity,
            self.min_dli,
            self.min_humidity,
            self.min_illuminance,
            self.min_moisture,
            self.min_temperature,
        ]

    @property
    def meter_entities(self) -> list[Entity]:
        """List all meter (sensor) entities"""
        return [
            self.sensor_conductivity,
            self.sensor_humidity,
            self.sensor_illuminance,
            self.sensor_moisture,
            self.sensor_temperature,
        ]

    @property
    def integral_entities(self) -> list(Entity):
        """List all integral entities"""
        return [
            self.dli,
            self.ppfd,
            self.total_integral,
        ]

    def add_image(self, image_url: str | None) -> None:
        """Set new entity_picture"""
        self._attr_entity_picture = image_url
        options = self._config.options.copy()
        options[ATTR_ENTITY_PICTURE] = image_url
        self._hass.config_entries.async_update_entry(self._config, options=options)

    def add_species(self, species: Entity | None) -> None:
        """Set new species"""
        self.species = species

    def add_thresholds(
        self,
        max_moisture: Entity | None,
        min_moisture: Entity | None,
        max_temperature: Entity | None,
        min_temperature: Entity | None,
        max_conductivity: Entity | None,
        min_conductivity: Entity | None,
        max_illuminance: Entity | None,
        min_illuminance: Entity | None,
        max_humidity: Entity | None,
        min_humidity: Entity | None,
        max_dli: Entity | None,
        min_dli: Entity | None,
    ) -> None:
        """Add the threshold entities"""
        self.max_moisture = max_moisture
        self.min_moisture = min_moisture
        self.max_temperature = max_temperature
        self.min_temperature = min_temperature
        self.max_conductivity = max_conductivity
        self.min_conductivity = min_conductivity
        self.max_illuminance = max_illuminance
        self.min_illuminance = min_illuminance
        self.max_humidity = max_humidity
        self.min_humidity = min_humidity
        self.max_dli = max_dli
        self.min_dli = min_dli

    def add_sensors(
        self,
        moisture: Entity | None,
        temperature: Entity | None,
        conductivity: Entity | None,
        illuminance: Entity | None,
        humidity: Entity | None,
    ) -> None:
        """Add the sensor entities"""
        self.sensor_moisture = moisture
        self.sensor_temperature = temperature
        self.sensor_conductivity = conductivity
        self.sensor_illuminance = illuminance
        self.sensor_humidity = humidity

    def add_dli(
        self,
        dli: Entity | None,
    ) -> None:
        """Add the DLI-utility sensors"""
        self.dli = dli
        self.plant_complete = True

    def add_calculations(self, ppfd: Entity, total_integral: Entity) -> None:
        """Add the intermediate calculation entities"""
        self.ppfd = ppfd
        self.total_integral = total_integral

    def update(self) -> None:
        """Run on every update of the entities"""

        new_state = STATE_OK
        known_state = False

        if self.sensor_moisture is not None:
            moisture = getattr(
                self._hass.states.get(self.sensor_moisture.entity_id), "state", None
            )
            if (
                moisture is not None
                and moisture != STATE_UNKNOWN
                and moisture != STATE_UNAVAILABLE
            ):
                known_state = True
                if float(moisture) < float(self.min_moisture.state):
                    self.moisture_status = STATE_LOW
                    if self.moisture_trigger:
                        new_state = STATE_PROBLEM
                elif float(moisture) > float(self.max_moisture.state):
                    self.moisture_status = STATE_HIGH
                    if self.moisture_trigger:
                        new_state = STATE_PROBLEM
                else:
                    self.moisture_status = STATE_OK

        if self.sensor_conductivity is not None:
            conductivity = getattr(
                self._hass.states.get(self.sensor_conductivity.entity_id), "state", None
            )
            if (
                conductivity is not None
                and conductivity != STATE_UNKNOWN
                and conductivity != STATE_UNAVAILABLE
            ):
                known_state = True
                if float(conductivity) < float(self.min_conductivity.state):
                    self.conductivity_status = STATE_LOW
                    if self.conductivity_trigger:
                        new_state = STATE_PROBLEM
                elif float(conductivity) > float(self.max_conductivity.state):
                    self.conductivity_status = STATE_HIGH
                    if self.conductivity_trigger:
                        new_state = STATE_PROBLEM
                else:
                    self.conductivity_status = STATE_OK

        if self.sensor_temperature is not None:
            temperature = getattr(
                self._hass.states.get(self.sensor_temperature.entity_id), "state", None
            )
            if (
                temperature is not None
                and temperature != STATE_UNKNOWN
                and temperature != STATE_UNAVAILABLE
            ):
                known_state = True
                if float(temperature) < float(self.min_temperature.state):
                    self.temperature_status = STATE_LOW
                    if self.temperature_trigger:
                        new_state = STATE_PROBLEM
                elif float(temperature) > float(self.max_temperature.state):
                    self.temperature_status = STATE_HIGH
                    if self.temperature_trigger:
                        new_state = STATE_PROBLEM
                else:
                    self.temperature_status = STATE_OK

        if self.sensor_humidity is not None:
            humidity = getattr(
                self._hass.states.get(self.sensor_humidity.entity_id), "state", None
            )
            if (
                humidity is not None
                and humidity != STATE_UNKNOWN
                and humidity != STATE_UNAVAILABLE
            ):
                known_state = True
                if float(humidity) < float(self.min_humidity.state):
                    self.humidity_status = STATE_LOW
                    if self.humidity_trigger:
                        new_state = STATE_PROBLEM
                elif float(humidity) > float(self.max_humidity.state):
                    self.humidity_status = STATE_HIGH
                    if self.humidity_trigger:
                        new_state = STATE_PROBLEM
                else:
                    self.humidity_status = STATE_OK

        # Check the instant values for illuminance against "max"
        # Ignoring "min" value for illuminance as it would probably trigger every night
        if self.sensor_illuminance is not None:
            illuminance = getattr(
                self._hass.states.get(self.sensor_illuminance.entity_id), "state", None
            )
            if (
                illuminance is not None
                and illuminance != STATE_UNKNOWN
                and illuminance != STATE_UNAVAILABLE
            ):
                known_state = True
                if float(illuminance) > float(self.max_illuminance.state):
                    self.illuminance_status = STATE_HIGH
                    if self.illuminance_trigger:
                        new_state = STATE_PROBLEM
                else:
                    self.illuminance_status = STATE_OK

        # - Checking Low values would create "problem" every night...
        # Check DLI from the previous day against max/min DLI
        if (
            self.dli is not None
            and self.dli.native_value != STATE_UNKNOWN
            and self.dli.native_value != STATE_UNAVAILABLE
            and self.dli.state is not None
        ):
            known_state = True
            if float(self.dli.extra_state_attributes["last_period"]) > 0 and float(
                self.dli.extra_state_attributes["last_period"]
            ) < float(self.min_dli.state):
                self.dli_status = STATE_LOW
                if self.dli_trigger:
                    new_state = STATE_PROBLEM
            elif float(self.dli.extra_state_attributes["last_period"]) > 0 and float(
                self.dli.extra_state_attributes["last_period"]
            ) > float(self.max_dli.state):
                self.dli_status = STATE_HIGH
                if self.dli_trigger:
                    new_state = STATE_PROBLEM
            else:
                self.dli_status = STATE_OK

        if not known_state:
            new_state = STATE_UNKNOWN

        self._attr_state = new_state
        self.update_registry()

    @property
    def data_source(self) -> str | None:
        """Currently unused. For future use"""
        return None

    def update_registry(self) -> None:
        """Update registry with correct data"""
        # Is there a better way to add an entity to the device registry?

        device_registry = dr.async_get(self._hass)
        device_registry.async_get_or_create(
            config_entry_id=self._config.entry_id,
            identifiers={(DOMAIN, self.unique_id)},
            name=self.name,
            model=self.display_species,
            manufacturer=self.data_source,
        )
        if self._device_id is None:
            device = device_registry.async_get_device(
                identifiers={(DOMAIN, self.unique_id)}
            )
            self._device_id = device.id

    async def async_added_to_hass(self) -> None:
        self.update_registry()
