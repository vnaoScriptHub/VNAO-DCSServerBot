from __future__ import annotations

import asyncio
import logging

from abc import ABC
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from core import Server

__all__ = ["Extension"]


class Extension(ABC):
    started_schedulers = set()

    def __init__(self, server: Server, config: dict):
        self.node = server.node
        self.log = logging.getLogger(__name__)
        self.pool = self.node.pool
        self.loop = asyncio.get_event_loop()
        self.config: dict = config
        self.server: Server = server
        self.locals: dict = self.load_config()
        self.running = False
        if self.__class__.__name__ not in Extension.started_schedulers:
            schedule = getattr(self, 'schedule', None)
            if schedule:
                schedule.start()
            Extension.started_schedulers.add(self.__class__.__name__)

    def load_config(self) -> Optional[dict]:
        return dict()

    async def prepare(self) -> bool:
        return True

    async def beforeMissionLoad(self, filename: str) -> tuple[str, bool]:
        return filename, False

    async def startup(self) -> bool:
        # avoid race conditions
        if await asyncio.to_thread(self.is_running):
            return True
        self.running = True
        self.log.info(f"  => {self.name} launched for \"{self.server.name}\".")
        return True

    def shutdown(self) -> bool:
        # avoid race conditions
        if not self.is_running():
            return True
        self.running = False
        self.log.info(f"  => {self.name} shut down for \"{self.server.name}\".")
        return True

    def is_running(self) -> bool:
        return self.running

    @property
    def name(self) -> str:
        return type(self).__name__

    @property
    def version(self) -> Optional[str]:
        return None

    async def render(self, param: Optional[dict] = None) -> dict:
        raise NotImplementedError()

    def is_installed(self) -> bool:
        ...
