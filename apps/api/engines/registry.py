from __future__ import annotations

from .base import Engine
from .gatling import GatlingEngine
from .jmeter import JMeterEngine
from .k6 import K6Engine
from .locust import LocustEngine
from .playwright import PlaywrightEngine

_ENGINE_MAP: dict[str, type[Engine]] = {
    "JMeter": JMeterEngine,
    "k6": K6Engine,
    "Gatling": GatlingEngine,
    "Locust": LocustEngine,
    "Playwright": PlaywrightEngine,
}


def get_engine(name: str) -> Engine | None:
    cls = _ENGINE_MAP.get(name)
    if cls is None:
        return None
    return cls()


def list_engines() -> list[str]:
    return list(_ENGINE_MAP.keys())
