import os

from core import Instance, Server
from core.services.registry import ServiceRegistry
from core.data.node import Node, UploadStatus, SortOrder
from core.data.proxy.instanceproxy import InstanceProxy
from pathlib import Path
from typing import Any, Union, Optional, Tuple

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

__all__ = ["NodeProxy"]


class NodeProxy(Node):
    def __init__(self, local_node: Any, name: str, public_ip: str, config_dir: Optional[str] = './config'):
        super().__init__(name, config_dir)
        self.local_node = local_node
        self.pool = self.local_node.pool
        self.log = self.local_node.log
        self._public_ip = public_ip
        self.locals = self.read_locals()
        self.bus = ServiceRegistry.get("ServiceBus")

    @property
    def master(self) -> bool:
        return False

    @master.setter
    def master(self, value: bool):
        raise NotImplemented()

    @property
    def public_ip(self) -> str:
        return self._public_ip

    @public_ip.setter
    def public_ip(self, public_ip: str):
        self._public_ip = public_ip

    @property
    def installation(self) -> str:
        raise NotImplemented()

    @property
    def extensions(self) -> dict:
        raise NotImplemented()

    def read_locals(self) -> dict:
        _locals = dict()
        config_file = os.path.join(self.config_dir, 'nodes.yaml')
        if os.path.exists(config_file):
            node: dict = yaml.load(Path(config_file).read_text(encoding='utf-8')).get(self.name)
            if not node:
                self.log.warning(f'No configuration found for node "{self.name}" in {config_file}!')
                return {}
            for name, element in node.items():
                if name == 'instances':
                    for _name, _element in node['instances'].items():
                        instance = InstanceProxy(self.local_node, _name)
                        instance.locals = _element
                        self.instances.append(instance)
                else:
                    _locals[name] = element
        return _locals

    async def upgrade_pending(self) -> bool:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "upgrade_pending"
        }, node=self.name, timeout=60)
        return data['return']

    async def upgrade(self):
        await self.bus.send_to_node({
            "command": "rpc",
            "object": "Node",
            "method": "upgrade"
        }, node=self.name)

    async def update(self, warn_times: list[int]) -> int:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "update",
            "params": {
                "warn_times": warn_times
            }
        }, node=self.name, timeout=600)
        return data['return']

    async def get_dcs_branch_and_version(self) -> Tuple[str, str]:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "get_dcs_branch_and_version"
        }, node=self.name)
        return data['return'][0], data['return'][1]

    async def handle_module(self, what: str, module: str) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "handle_module"
        }, node=self.name)

    async def get_installed_modules(self) -> list[str]:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "get_installed_modules"
        }, node=self.name)
        return data['return']

    async def get_available_modules(self, userid: Optional[str] = None, password: Optional[str] = None) -> list[str]:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "get_available_modules"
        }, timeout=60, node=self.name)
        return data['return']

    async def shell_command(self, cmd: str) -> Optional[Tuple[str, str]]:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "shell_command",
            "params": {
                "cmd": cmd
            }
        }, timeout=60, node=self.name)
        return data['return']

    async def read_file(self, path: str) -> Union[bytes, int]:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "read_file",
            "params": {
                "path": path
            }
        }, timeout=60, node=self.name)
        with self.pool.connection() as conn:
            with conn.transaction():
                file = conn.execute("SELECT data FROM files WHERE id = %s", (data['return'], ),
                                    binary=True).fetchone()[0]
                conn.execute("DELETE FROM files WHERE id = %s", (data['return'], ))
        return file

    async def write_file(self, filename: str, url: str, overwrite: bool = False) -> UploadStatus:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "write_file",
            "params": {
                "filename": filename,
                "url": url,
                "overwrite": overwrite
            }
        }, timeout=60, node=self.name)
        return UploadStatus(data["return"])

    async def list_directory(self, path: str, pattern: str, order: Optional[SortOrder] = SortOrder.DATE) -> list[str]:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "list_directory",
            "params": {
                "path": path,
                "pattern": pattern,
                "order": order.value
            }
        }, node=self.name)
        return data['return']

    async def remove_file(self, path: str):
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "remove_file",
            "params": {
                "path": path
            }
        }, node=self.name)

    async def rename_file(self, old_name: str, new_name: str, *, force: Optional[bool] = False):
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "rename_file",
            "params": {
                "old_name": old_name,
                "new_name": new_name,
                "force": force
            }
        }, node=self.name)

    async def rename_server(self, server: Server, new_name: str, update_settings: Optional[bool] = False):
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "rename_server",
            "params": {
                "server": server.name,
                "new_name": new_name,
                "update_settings": update_settings
            }
        }, node=self.name)

    async def add_instance(self, name: str, *, template: Optional[Instance] = None) -> Instance:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "add_instance",
            "params": {
                "name": name,
                "template": template.name
            }
        }, node=self.name)
        return InstanceProxy(name=data['return'], node=self)

    async def delete_instance(self, instance: Instance, remove_files: bool) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "delete_instance",
            "params": {
                "instance": instance.name,
                "remove_files": remove_files
            }
        }, node=self.name)

    async def rename_instance(self, instance: Instance, new_name: str) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "rename_instance",
            "params": {
                "instance": instance.name,
                "new_name": new_name
            }
        }, node=self.name)

    async def find_all_instances(self) -> list[Tuple[str, str]]:
        data = await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "find_all_instances"
        }, node=self.name)
        return data['return']

    async def migrate_server(self, server: Server, instance: Instance):
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "migrate_server",
            "params": {
                "server": server.name,
                "instance": instance.name
            }
        }, node=self.name)

    async def unregister_server(self, server: Server) -> None:
        await self.bus.send_to_node_sync({
            "command": "rpc",
            "object": "Node",
            "method": "unregister_server",
            "params": {
                "server": server.name
            }
        }, node=self.name)
