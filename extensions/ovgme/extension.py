import asyncio

from core import Extension, MizFile, Server, UnsupportedMizFileException
from typing import Optional

__all__ = [
    "OvGME"
]


class OvGME(Extension):
    def __init__(self, server: Server, config: dict):
        super().__init__(server, config)
        self.modules: dict[str, list[str]] = {}

    async def startup(self) -> bool:
        await super().startup()
        filename = await self.server.get_current_mission_file()
        try:
            mission = await asyncio.to_thread(MizFile, filename)
            self.modules[self.server.name] = mission.requiredModules
        except UnsupportedMizFileException:
            self.log.warning(f"Can't read requiredModules from Mission {filename}, unsupported format.")
        return True

    def shutdown(self) -> bool:
        self.modules.pop(self.server.name, None)
        return super().shutdown()

    async def render(self, param: Optional[dict] = None) -> dict:
        mods = self.modules.get(self.server.name)
        if mods:
            return {
                "name": "Required Mods",
                "value": '\n'.join([f"- {mod}" for mod in mods])
            }
        else:
            raise NotImplementedError()
