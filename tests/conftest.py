"""Load pure timer modules and HA contract doubles; related files: all unit tests.

These doubles model the public interfaces checked against HA 2026.10.1 source.
They are NOT an installed/running Home Assistant or real satellite validation.
"""

import importlib
import sys
from dataclasses import dataclass
from enum import IntFlag
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "assist_tools_unit"
package = ModuleType(PACKAGE)
package.__path__ = [str(ROOT / "custom_components" / "assist_tools")]
sys.modules[PACKAGE] = package


def module(name, **members):
    result = ModuleType(name)
    result.__path__ = []
    result.__dict__.update(members)
    sys.modules[name] = result
    if "." in name and name.rsplit(".", 1)[0] in sys.modules:
        setattr(sys.modules[name.rsplit(".", 1)[0]], name.rsplit(".", 1)[1], result)
    return result


@dataclass
class LLMContext:
    device_id: str | None
    platform: str = "test"
    context: object = None
    language: str = "ko"
    assistant: str = "conversation"


@dataclass
class ToolInput:
    tool_name: str
    tool_args: dict
    id: str = "call-1"


@dataclass
class ToolResult:
    data: dict
    error: bool = False


@dataclass
class API:
    hass: object
    id: str
    name: str


@dataclass
class APIInstance:
    api: object
    api_prompt: str
    llm_context: object
    tools: list


class PlayerFeature(IntFlag):
    STOP = 4096
    PLAY_MEDIA = 512


class SatelliteFeature(IntFlag):
    ANNOUNCE = 1


class ClimateFeature(IntFlag):
    """Climate feature contract; related files: climate_tools and climate tests."""

    FAN_MODE = 8


class FanFeature(IntFlag):
    """Fan contract constants; related files: fan_tools and test_fan_tools."""

    SET_SPEED = 1
    OSCILLATE = 2
    DIRECTION = 4
    PRESET_MODE = 8


class ConfigFlow:
    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__()


class Store:
    def __init__(self, hass, version, key):
        self.hass, self.key = hass, key

    async def async_load(self):
        return self.hass.storage.get(self.key)

    async def async_save(self, data):
        self.hass.storage[self.key] = data

    async def async_remove(self):
        self.hass.storage.pop(self.key, None)


class SupportsResponse:
    OPTIONAL = "optional"


@pytest.fixture(scope="session")
def ha_contract():
    """Provide minimal verified HA boundaries for adapter/provider unit tests."""
    module("homeassistant")
    module("homeassistant.auth")
    module("homeassistant.auth.permissions")
    module("homeassistant.auth.permissions.const", POLICY_CONTROL="control", POLICY_READ="read")
    module(
        "homeassistant.core",
        HomeAssistant=object,
        Context=lambda **kwargs: SimpleNamespace(**kwargs),
        ServiceCall=object,
        SupportsResponse=SupportsResponse,
        callback=lambda f: f,
    )
    module(
        "homeassistant.const",
        STATE_UNAVAILABLE="unavailable",
        STATE_UNKNOWN="unknown",
        EVENT_HOMEASSISTANT_STOP="homeassistant_stop",
    )
    module(
        "homeassistant.exceptions",
        ConfigEntryNotReady=type("ConfigEntryNotReady", (Exception,), {}),
        ServiceValidationError=type("ServiceValidationError", (Exception,), {}),
    )
    module("homeassistant.config_entries", ConfigEntry=object, ConfigFlow=ConfigFlow)
    module("homeassistant.helpers")
    module("homeassistant.helpers.entity_platform", AddEntitiesCallback=object)
    module("homeassistant.helpers.entity_registry", async_get=lambda hass: hass.registry)
    module("homeassistant.helpers.storage", Store=Store)
    module("homeassistant.helpers.typing", ConfigType=dict)
    module(
        "homeassistant.helpers.selector",
        EntitySelector=lambda config: str,
        EntitySelectorConfig=lambda **kwargs: kwargs,
        SelectSelector=lambda config: str,
        SelectSelectorConfig=lambda **kwargs: kwargs,
        SelectSelectorMode=SimpleNamespace(DROPDOWN="dropdown"),
    )
    module("homeassistant.helpers.network", get_url=lambda hass, **kwargs: "http://ha.test:8123")
    apis = {}

    def register_api(hass, api):
        if api.id in apis:
            raise ValueError("Duplicate API")
        apis[api.id] = api
        return lambda: apis.pop(api.id)

    module(
        "homeassistant.helpers.llm",
        Tool=object,
        ToolAnnotations=SimpleNamespace,
        API=API,
        APIInstance=APIInstance,
        ToolInput=ToolInput,
        ToolResult=ToolResult,
        LLMContext=LLMContext,
        async_register_api=register_api,
    )
    module("homeassistant.components")
    module("homeassistant.components.homeassistant")
    module(
        "homeassistant.components.homeassistant.exposed_entities",
        async_should_expose=lambda hass, assistant, entity_id: entity_id in hass.exposed,
    )
    module(
        "homeassistant.components.assist_satellite", AssistSatelliteEntityFeature=SatelliteFeature
    )
    module("homeassistant.components.media_player", MediaPlayerEntityFeature=PlayerFeature)
    module("homeassistant.components.climate", ClimateEntityFeature=ClimateFeature)
    module("homeassistant.components.fan", FanEntityFeature=FanFeature)
    module("homeassistant.components.weather", DOMAIN="weather")
    module("homeassistant.components.recorder")
    module(
        "homeassistant.components.recorder.history",
        get_significant_states=lambda *args, **kwargs: {},
    )
    module("homeassistant.helpers.recorder", get_instance=lambda hass: hass.data["test_recorder"])
    module("homeassistant.components.http", StaticPathConfig=lambda *args: args)
    return SimpleNamespace(
        apis=apis,
        LLMContext=LLMContext,
        ToolInput=ToolInput,
    )


@pytest.fixture
def load_module():
    return lambda name: importlib.import_module(f"{PACKAGE}.{name}")


@pytest.fixture
def hass(ha_contract):
    states = {
        "assist_satellite.kitchen": SimpleNamespace(
            entity_id="assist_satellite.kitchen",
            state="idle",
            name="Kitchen",
            attributes={"supported_features": 1},
        ),
        "media_player.kitchen": SimpleNamespace(
            entity_id="media_player.kitchen",
            name="Kitchen",
            state="idle",
            attributes={"supported_features": 4608},
        ),
    }
    entries = {
        "assist_satellite.kitchen": SimpleNamespace(device_id="kitchen", platform="esphome"),
    }
    return SimpleNamespace(
        data={},
        config=SimpleNamespace(time_zone="Asia/Seoul"),
        states=SimpleNamespace(
            get=states.get,
            async_all=lambda domain=None: [
                state
                for key, state in states.items()
                if domain is None or key.startswith(domain + ".")
            ],
        ),
        state_values=states,
        registry=SimpleNamespace(async_get=entries.get),
        registry_values=entries,
        services=SimpleNamespace(
            async_call=AsyncMock(),
            async_register=lambda *args, **kwargs: None,
            has_service=lambda domain, action: True,
        ),
        exposed=set(),
        auth=SimpleNamespace(
            async_get_user=AsyncMock(
                return_value=SimpleNamespace(
                    is_active=True,
                    permissions=SimpleNamespace(check_entity=lambda entity, policy: True),
                )
            )
        ),
        storage={},
        http=SimpleNamespace(async_register_static_paths=AsyncMock()),
        bus=SimpleNamespace(async_listen_once=lambda *args: None),
        config_entries=SimpleNamespace(
            async_entries=lambda domain: [],
        ),
    )
