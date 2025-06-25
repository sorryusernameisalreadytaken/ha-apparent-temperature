"""Sensor platform for apparent_temperature."""

import logging
import math
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.components.climate import (
    ATTR_CURRENT_HUMIDITY,
    ATTR_CURRENT_TEMPERATURE,
    DOMAIN as CLIMATE_DOMAIN,
)
from homeassistant.components.group import expand_entity_ids
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.components.weather import (
    ATTR_WEATHER_HUMIDITY,
    ATTR_WEATHER_TEMPERATURE,
    ATTR_WEATHER_TEMPERATURE_UNIT,
    ATTR_WEATHER_WIND_SPEED,
    ATTR_WEATHER_WIND_SPEED_UNIT,
    DOMAIN as WEATHER_DOMAIN,
)
from homeassistant.const import (
    ATTR_DEVICE_CLASS,
    ATTR_UNIT_OF_MEASUREMENT,
    CONF_NAME,
    CONF_SOURCE,
    CONF_UNIQUE_ID,
    EVENT_HOMEASSISTANT_START,
    PERCENTAGE,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfSpeed,
    UnitOfTemperature,
)
from homeassistant.core import Event, HomeAssistant, State, callback, split_entity_id
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.typing import ConfigType, DiscoveryInfoType, UndefinedType
from homeassistant.util.unit_conversion import SpeedConverter, TemperatureConverter

from .const import (
    ATTR_HUMIDITY_SOURCE,
    ATTR_HUMIDITY_SOURCE_VALUE,
    ATTR_TEMPERATURE_SOURCE,
    ATTR_TEMPERATURE_SOURCE_VALUE,
    ATTR_WIND_SPEED_SOURCE,
    ATTR_WIND_SPEED_SOURCE_VALUE,
    STARTUP_MESSAGE,
)

_LOGGER = logging.getLogger(__name__)

PLATFORM_SCHEMA = cv.PLATFORM_SCHEMA.extend(
    {
        vol.Required(CONF_SOURCE): cv.entity_ids,
        vol.Optional(CONF_NAME): cv.string,
        vol.Optional(CONF_UNIQUE_ID): cv.string,
    }
)


async def async_setup_platform(
    hass: HomeAssistant,
    config: ConfigType,
    async_add_entities: AddEntitiesCallback,
    discovery_info: DiscoveryInfoType | None = None,
) -> None:
    """Set up the Apparent Temperature sensor platform."""
    _LOGGER.info(STARTUP_MESSAGE)

    async_add_entities(
        [
            ApparentTemperatureSensor(
                config.get(CONF_UNIQUE_ID),
                config.get(CONF_NAME),
                expand_entity_ids(hass, config.get(CONF_SOURCE)),
            )
        ]
    )


class ApparentTemperatureSensor(SensorEntity):
    """Apparent Temperature Sensor class."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:thermometer-lines"
    _attr_device_class = SensorDeviceClass.TEMPERATURE
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_should_poll = False
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_suggested_display_precision = 1

    def __init__(
        self, unique_id: str | None, name: str | None, sources: list[str]
    ) -> None:
        self._attr_unique_id = unique_id
        self._attr_native_value = None
        self._name = name
        self._sources = sources

        self._temp = None
        self._humd = None
        self._wind = None
        self._temp_val = None
        self._humd_val = None
        self._wind_val = None

    @property
    def name(self) -> str | UndefinedType | None:
        if self._name:
            return self._name
        return self._compose_name(split_entity_id(self._sources[0])[1])

    @staticmethod
    def _compose_name(source_name: str) -> str:
        tpos = source_name.rfind("temperature")
        return (
            source_name + " Apparent Temperature"
            if tpos < 0
            else source_name[:tpos] + "Apparent " + source_name[tpos:]
        )

    @property
    def extra_state_attributes(self) -> Mapping[str, Any] | None:
        return {
            ATTR_TEMPERATURE_SOURCE: self._temp,
            ATTR_TEMPERATURE_SOURCE_VALUE: self._temp_val,
            ATTR_HUMIDITY_SOURCE: self._humd,
            ATTR_HUMIDITY_SOURCE_VALUE: self._humd_val,
            ATTR_WIND_SPEED_SOURCE: self._wind,
            ATTR_WIND_SPEED_SOURCE_VALUE: self._wind_val,
        }

    def _setup_sources(self) -> list[str]:
        """Select best-matching sources from given list."""
        found = {
            "temp": None,
            "humd": None,
            "wind": None,
        }

        for entity_id in self._sources:
            state: State = self.hass.states.get(entity_id)
            if state is None:
                continue

            domain = split_entity_id(entity_id)[0]
            attrs = state.attributes
            unit = attrs.get(ATTR_UNIT_OF_MEASUREMENT)
            device_class = attrs.get(ATTR_DEVICE_CLASS)

            # Handle weather entity (can supply all three)
            if domain == WEATHER_DOMAIN:
                if not found["temp"]:
                    found["temp"] = entity_id
                if not found["humd"]:
                    found["humd"] = entity_id
                if not found["wind"]:
                    found["wind"] = entity_id
                continue

            # Temperature preference
            if not found["temp"] and (
                device_class == SensorDeviceClass.TEMPERATURE
                or unit in UnitOfTemperature
                or "temperature" in entity_id.lower()
            ):
                found["temp"] = entity_id

            # Humidity preference
            if not found["humd"] and (
                device_class == SensorDeviceClass.HUMIDITY
                or unit == PERCENTAGE
                or "humidity" in entity_id.lower()
            ):
                found["humd"] = entity_id

            # Wind speed preference
            if not found["wind"] and (
                unit in UnitOfSpeed or "wind" in entity_id.lower()
            ):
                found["wind"] = entity_id

        self._temp = found["temp"]
        self._humd = found["humd"]
        self._wind = found["wind"]
        return [s for s in [self._temp, self._humd, self._wind] if s]

    async def async_added_to_hass(self) -> None:
        @callback
        def sensor_state_listener(event: Event) -> None:
            self.async_schedule_update_ha_state(force_refresh=True)

        @callback
        def sensor_startup(event: Event) -> None:
            async_track_state_change_event(
                self.hass, self._setup_sources(), sensor_state_listener
            )
            self.async_schedule_update_ha_state(force_refresh=True)

        self.hass.bus.async_listen_once(EVENT_HOMEASSISTANT_START, sensor_startup)

    @staticmethod
    def _has_state(state: str | None) -> bool:
        return state is not None and state not in [
            STATE_UNKNOWN,
            STATE_UNAVAILABLE,
            "None",
            "",
        ]

    def _get_temperature(self, entity_id: str | None) -> float | None:
        if entity_id is None:
            return None
        state: State = self.hass.states.get(entity_id)
        if state is None:
            return None

        domain = split_entity_id(state.entity_id)[0]
        if domain == WEATHER_DOMAIN:
            value = state.attributes.get(ATTR_WEATHER_TEMPERATURE)
            unit = state.attributes.get(ATTR_WEATHER_TEMPERATURE_UNIT)
        elif domain == CLIMATE_DOMAIN:
            value = state.attributes.get(ATTR_CURRENT_TEMPERATURE)
            unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
        else:
            value = state.state
            unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)

        if not self._has_state(value):
            return None

        try:
            return TemperatureConverter.convert(
                float(value), unit, UnitOfTemperature.CELSIUS
            )
        except ValueError:
            _LOGGER.exception("Temperature conversion failed for %s", state)
            return None

    def _get_humidity(self, entity_id: str | None) -> float | None:
        if entity_id is None:
            return None
        state: State = self.hass.states.get(entity_id)
        if state is None:
            return None

        domain = split_entity_id(state.entity_id)[0]
        if domain == WEATHER_DOMAIN:
            value = state.attributes.get(ATTR_WEATHER_HUMIDITY)
        elif domain == CLIMATE_DOMAIN:
            value = state.attributes.get(ATTR_CURRENT_HUMIDITY)
        else:
            value = state.state

        if not self._has_state(value):
            return None

        return float(value)

    def _get_wind_speed(self, entity_id: str | None) -> float | None:
        if entity_id is None:
            return 0.0
        state: State = self.hass.states.get(entity_id)
        if state is None:
            return 0.0

        domain = split_entity_id(state.entity_id)[0]
        if domain == WEATHER_DOMAIN:
            value = state.attributes.get(ATTR_WEATHER_WIND_SPEED)
            unit = state.attributes.get(ATTR_WEATHER_WIND_SPEED_UNIT)
        else:
            value = state.state
            unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)

        if not self._has_state(value):
            return None

        try:
            return SpeedConverter.convert(
                float(value), unit, UnitOfSpeed.METERS_PER_SECOND
            )
        except ValueError:
            _LOGGER.exception("Wind speed conversion failed for %s", state)
            return None

    async def async_update(self) -> None:
        self._temp_val = temp = self._get_temperature(self._temp)
        self._humd_val = humd = self._get_humidity(self._humd)
        self._wind_val = wind = self._get_wind_speed(self._wind)

        _LOGGER.debug("Temp: %s °C  Hum: %s %%  Wind: %s m/s", temp, humd, wind)

        if temp is None or humd is None:
            _LOGGER.warning("Can't calculate sensor value: missing temp/humidity")
            self._attr_native_value = None
            return

        if wind is None:
            wind = 0.0

        e = humd * 0.06105 * math.exp((17.27 * temp) / (237.7 + temp))
        self._attr_native_value = temp + 0.348 * e - 0.7 * wind - 4.25

        _LOGGER.debug("New sensor state: %s °C", self._attr_native_value)
