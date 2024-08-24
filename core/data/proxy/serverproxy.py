from __future__ import annotations
from core import Server, Status, utils, Coalition
from core.data.node import UploadStatus
from dataclasses import dataclass, field
from typing import Optional, Union, Any

__all__ = ["ServerProxy"]


@dataclass
class ServerProxy(Server):
    _extensions: Optional[list[dict]] = field(compare=False, default=None)

    async def reload(self):
        timeout = 60 if not self.node.slow_system else 120
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "reload",
            "server_name": self.name
        }, node=self.node.name, timeout=timeout)

    @property
    def is_remote(self) -> bool:
        return True

    async def get_missions_dir(self) -> str:
        timeout = 60 if not self.node.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "get_missions_dir",
            "server_name": self.name
        }, node=self.node.name, timeout=timeout)
        return data["return"]

    @property
    def settings(self) -> dict:
        return self._settings

    @settings.setter
    def settings(self, s: dict):
        self._settings = utils.RemoteSettingsDict(self, "_settings", s)

    @property
    def options(self) -> dict:
        return self._options

    @options.setter
    def options(self, o: dict):
        self._options = utils.RemoteSettingsDict(self, "_options", o)

    async def get_current_mission_file(self) -> Optional[str]:
        timeout = 60 if not self.node.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "get_current_mission_file",
            "server_name": self.name
        }, node=self.node.name, timeout=timeout)
        return data["return"]

    async def get_current_mission_theatre(self) -> Optional[str]:
        timeout = 120 if not self.node.slow_system else 240
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "get_current_mission_theatre",
            "server_name": self.name
        }, node=self.node.name, timeout=timeout)
        return data["return"]

    async def send_to_dcs(self, message: dict):
        message['server_name'] = self.name
        await self.bus.send_to_node(message, node=self.node.name)

    async def startup(self, modify_mission: Optional[bool] = True) -> None:
        timeout = 180 if not self.node.slow_system else 300
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "startup",
            "modify_mission": modify_mission,
            "server_name": self.name
        }, timeout=timeout, node=self.node.name)

    async def startup_extensions(self) -> None:
        timeout = 180 if not self.node.slow_system else 300
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "startup_extensions",
            "server_name": self.name
        }, node=self.node.name, timeout=timeout)
        self._extensions = None

    async def shutdown_extensions(self) -> None:
        timeout = 180 if not self.node.slow_system else 300
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "shutdown_extensions",
            "server_name": self.name
        }, node=self.node.name, timeout=timeout)
        self._extensions = None

    async def shutdown(self, force: bool = False) -> None:
        timeout = 180 if not self.node.slow_system else 300
        await super().shutdown(force)
        if self.status != Status.SHUTDOWN:
            await self.bus.send_to_node_sync({
                "command": "rpc",
                "object": "Server",
                "method": "shutdown",
                "server_name": self.name,
                "params": {
                    "force": force
                },
            }, node=self.node.name, timeout=timeout)
            self.status = Status.SHUTDOWN

    async def init_extensions(self) -> list[str]:
        timeout = 180 if not self.node.slow_system else 300
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "init_extensions",
            "server_name": self.name
        }, node=self.node.name, timeout=timeout)
        return data['return']

    async def prepare_extensions(self):
        timeout = 180 if not self.node.slow_system else 300
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "prepare_extensions",
            "server_name": self.name
        }, node=self.node.name, timeout=timeout)

    async def uploadMission(self, filename: str, url: str, force: bool = False) -> UploadStatus:
        timeout = 120 if not self.node.slow_system else 240
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "uploadMission",
            "params": {
                "filename": filename,
                "url": url,
                "force": force
            },
            "server_name": self.name
        }, timeout=timeout, node=self.node.name)
        return UploadStatus(data["return"])

    async def listAvailableMissions(self) -> list[str]:
        timeout = 60 if not self.node.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "listAvailableMissions",
            "server_name": self.name
        }, timeout=timeout, node=self.node.name)
        return data['return']

    async def getMissionList(self) -> list[str]:
        timeout = 60 if not self.node.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "getMissionList",
            "server_name": self.name
        }, timeout=timeout, node=self.node.name)
        return data['return']

    async def apply_mission_changes(self, filename: Optional[str] = None) -> str:
        timeout = 120 if not self.node.slow_system else 240
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "apply_mission_changes",
            "server_name": self.name,
            "params": {
                "filename": filename or ""
            }
        }, timeout=timeout, node=self.node.name)
        return data['return']

    async def modifyMission(self, filename: str, preset: Union[list, dict]) -> str:
        timeout = 120 if not self.node.slow_system else 240
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "modifyMission",
            "server_name": self.name,
            "params": {
                "filename": filename,
                "preset": preset
            }
        }, timeout=timeout, node=self.node.name)
        return data['return']

    async def persist_settings(self):
        timeout = 60 if not self.node.slow_system else 120
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "persist_settings",
            "server_name": self.name
        }, node=self.node.name, timeout=timeout)

    async def rename(self, new_name: str, update_settings: bool = False) -> None:
        timeout = 60 if not self.node.slow_system else 120
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "rename",
            "server_name": self.name,
            "params": {
                "new_name": new_name,
                "update_settings": update_settings
            }
        }, node=self.node.name, timeout=timeout)
        self.name = new_name

    async def render_extensions(self) -> list:
        if not self._extensions:
            timeout = 60 if not self.node.slow_system else 120
            data = await self.bus.send_to_node_sync({
                "command": "rpc",
                "object": "Server",
                "method": "render_extensions",
                "server_name": self.name
            }, timeout=timeout, node=self.node.name)
            self._extensions = data['return']
        return self._extensions

    async def is_running(self) -> bool:
        timeout = 60 if not self.node.slow_system else 120
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "is_running",
            "server_name": self.name
        }, timeout=timeout, node=self.node.name)
        return data['return']

    async def restart(self, modify_mission: Optional[bool] = True) -> None:
        timeout = 180 if not self.node.slow_system else 300
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "restart",
            "server_name": self.name,
            "params": {
                "modify_mission": modify_mission
            }
        }, timeout=timeout, node=self.node.name)

    async def setStartIndex(self, mission_id: int) -> None:
        timeout = 60 if not self.node.slow_system else 120
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "setStartIndex",
            "server_name": self.name,
            "params": {
                "mission_id": mission_id
            }
        }, timeout=timeout, node=self.node.name)

    async def setPassword(self, password: str):
        timeout = 60 if not self.node.slow_system else 120
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "setPassword",
            "server_name": self.name,
            "params": {
                "password": password
            }
        }, timeout=timeout, node=self.node.name)

    async def setCoalitionPassword(self, coalition: Coalition, password: str):
        timeout = 60 if not self.node.slow_system else 120
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "setCoalitionPassword",
            "server_name": self.name,
            "params": {
                "coalition": coalition.value,
                "password": password
            }
        }, timeout=timeout, node=self.node.name)

    async def addMission(self, path: str, *, autostart: Optional[bool] = False) -> None:
        timeout = 60 if not self.node.slow_system else 120
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "addMission",
            "server_name": self.name,
            "params": {
                "path": path,
                "autostart": autostart
            }
        }, timeout=timeout, node=self.node.name)

    async def deleteMission(self, mission_id: int) -> None:
        timeout = 60 if not self.node.slow_system else 120
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "deleteMission",
            "server_name": self.name,
            "params": {
                "mission_id": mission_id
            }
        }, timeout=timeout, node=self.node.name)

    async def replaceMission(self, mission_id: int, path: str) -> None:
        timeout = 60 if not self.node.slow_system else 120
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "replaceMission",
            "server_name": self.name,
            "params": {
                "mission_id": mission_id,
                "path": path
            }
        }, timeout=timeout, node=self.node.name)

    async def loadMission(self, mission: Union[int, str], modify_mission: Optional[bool] = True) -> None:
        timeout = 180 if not self.node.slow_system else 300
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "loadMission",
            "server_name": self.name,
            "params": {
                "mission": mission,
                "modify_mission": modify_mission
            }
        }, timeout=timeout, node=self.node.name)

    async def loadNextMission(self, modify_mission: Optional[bool] = True) -> None:
        timeout = 180 if not self.node.slow_system else 300
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "loadNextMission",
            "server_name": self.name,
            "params": {
                "modify_mission": modify_mission
            }
        }, timeout=timeout, node=self.node.name)

    async def run_on_extension(self, extension: str, method: str, **kwargs) -> Any:
        timeout = 180 if not self.node.slow_system else 300
        params = {
            "extension": extension,
            "method": method
        } | kwargs
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "run_on_extension",
            "server_name": self.name,
            "params": params
        }, timeout=timeout, node=self.node.name)
        return data['return']

    async def config_extension(self, name: str, config: dict) -> None:
        timeout = 60 if not self.node.slow_system else 120
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "config_extension",
            "server_name": self.name,
            "params": {
                "name": name,
                "config": config
            }
        }, timeout=timeout, node=self.node.name)

    async def install_extension(self, name: str, config: dict) -> None:
        timeout = 180 if not self.node.slow_system else 300
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "install_extension",
            "server_name": self.name,
            "params": {
                "name": name,
                "config": config
            }
        }, timeout=timeout, node=self.node.name)

    async def uninstall_extension(self, name: str) -> None:
        timeout = 180 if not self.node.slow_system else 300
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Server",
            "method": "uninstall_extension",
            "server_name": self.name,
            "params": {
                "name": name
            }
        }, timeout=timeout, node=self.node.name)
