from __future__ import annotations
import asyncio
import logging
import os
import psutil
import sys
if sys.platform == 'win32':
    import win32gui
    import win32process
    from minidump.utils.createminidump import create_dump, MINIDUMP_TYPE

from datetime import datetime, timezone
from discord.ext import tasks
from typing import TYPE_CHECKING, Union

from core import Status, utils, Server, ServerImpl, Autoexec
from core.services.base import Service
from core.services.registry import ServiceRegistry

if TYPE_CHECKING:
    from services import ServiceBus, DCSServerBot

__all__ = [
    "MonitoringService"
]


@ServiceRegistry.register("Monitoring")
class MonitoringService(Service):
    def __init__(self, node, name: str):
        super().__init__(node, name)
        self.bus: ServiceBus = ServiceRegistry.get("ServiceBus")
        self.io_counters = {}
        self.net_io_counters = None

    async def start(self):
        await super().start()
        self.check_autoexec()
        self.monitoring.start()

    async def stop(self):
        self.monitoring.cancel()
        await super().stop()

    def check_autoexec(self):
        for instance in self.node.instances:
            try:
                cfg = Autoexec(instance)
                if cfg.crash_report_mode is None:
                    self.log.info('  => Adding crash_report_mode = "silent" to autoexec.cfg')
                    cfg.crash_report_mode = 'silent'
                elif cfg.crash_report_mode != 'silent':
                    self.log.warning('=> crash_report_mode is NOT "silent" in your autoexec.cfg! DCSServerBot '
                                     'will not work properly on DCS crashes, please change it manually to "silent" '
                                     'to avoid that.')
            except Exception as ex:
                self.log.error(f"  => Error while parsing autoexec.cfg: {ex.__repr__()}")

    async def check_nodes(self):
        active_nodes: list[str] = self.node.get_active_nodes()
        used_nodes: set[str] = set()
        for server in [x for x in self.bus.servers.values() if x.is_remote]:
            if server.node.name not in active_nodes:
                self.log.warning(f"- Node {server.node.name} not responding, removing server {server.name}.")
                self.bus.servers[server.name].status = Status.UNREGISTERED
                del self.bus.servers[server.name]
            else:
                used_nodes.add(server.node.name)
        # any new nodes detected?
        for node in set(active_nodes) - used_nodes:
            await self.bus.register_remote_node(node)

    @staticmethod
    async def check_affinity(server: Server, affinity: Union[list[int], str]):
        if isinstance(affinity, str):
            affinity = [int(x.strip()) for x in affinity.split(',')]
        if not server.process:
            server.process = utils.find_process("DCS_server.exe|DCS.exe", server.instance.name)
        if server.process:
            server.process.cpu_affinity(affinity)

    async def warn_admins(self, server: Server, message: str) -> None:
        message += f"\nLatest dcs-<timestamp>.log can be pulled with /download\n" \
                   f"If the scheduler is configured for this server, it will relaunch it automatically."
        self.bus.send_to_node({
            "command": "rpc",
            "service": "Bot",
            "method": "alert",
            "params": {
                "server": server.name,
                "message": message
            }
        })

    async def check_popups(self):
        # check for blocked processes due to window popups
        for title in [
            "Can't run",
            "Login Failed",
            "DCS Login",
            "Authorization failed",
            "Login session has expired"
        ]:
            handle = win32gui.FindWindowEx(None, None, None, title)
            if handle:
                _, pid = win32process.GetWindowThreadProcessId(handle)
                for server in [x for x in self.bus.servers.values() if not x.is_remote]:
                    if server.process and server.process.pid == pid:
                        await server.shutdown(force=True)
                        await self.node.audit(f'Server killed due to a popup with title "{title}".',
                                              server=server)

    async def kill_hung_server(self, server: Server):
        message = (f"Can't reach server \"{server.name}\" for more than "
                   f"{int(server.instance.locals.get('max_hung_minutes', 3))} minutes. Killing ...")
        self.log.warning(message)
        if server.process and server.process.is_running():
            now = datetime.now(timezone.utc)
            filename = os.path.join(server.instance.home, 'Logs',
                                    f"{now.strftime('dcs-%Y%m%d-%H%M%S')}.dmp")
            if sys.platform == 'win32':
                await asyncio.to_thread(create_dump, server.process.pid, filename,
                                        MINIDUMP_TYPE.MiniDumpNormal, True)
                root = logging.getLogger()
                if root.handlers:
                    root.removeHandler(root.handlers[0])
            server.process.kill()
        else:
            await server.shutdown(True)
        server.process = None
        await self.node.audit("Server killed due to a hung state.", server=server)
        server.status = Status.SHUTDOWN
        if server.locals.get('ping_admin_on_crash', True):
            await self.warn_admins(server, message)

    async def heartbeat(self):
        for server in list(self.bus.servers.values()):  # type: ServerImpl
            # don't test remote servers or servers that are not initialized or shutdown
            if server.is_remote or server.status in [Status.UNREGISTERED, Status.SHUTDOWN]:
                continue
            # check if the process is dead (on load it might take some seconds for the process to appear)
            if server.process and not await server.is_running():
                message = f'Server "{server.name}" died. Setting state to SHUTDOWN.'
                self.log.warning(message)
                server.status = Status.SHUTDOWN
                if server.locals.get('ping_admin_on_crash', True):
                    await self.warn_admins(server, message)
                await self.node.audit(f'Server died.', server=server)
                return
            # No, check if the process is still doing something
            try:
                server.keep_alive()
                # check if server is alive
                if server.status == Status.LOADING:
                    max_hung = int(server.instance.locals.get('max_hung_minutes', 3)) * 2
                else:
                    max_hung = int(server.instance.locals.get('max_hung_minutes', 3))
                if (datetime.now(timezone.utc) - server.last_seen).total_seconds() / 60 > max_hung:
                    await self.kill_hung_server(server)
                    continue
                if server.status in [Status.RUNNING, Status.PAUSED]:
                    # check affinity
                    if 'affinity' in server.instance.locals:
                        await self.check_affinity(server, server.instance.locals['affinity'])
                    # check extension states
                    for ext in [x for x in server.extensions.values() if not x.is_running()]:
                        try:
                            await ext.startup()
                        except Exception as ex:
                            self.log.exception(ex)
            except Exception as ex:
                self.log.exception(ex)

    async def serverload(self):
        for server in self.bus.servers.values():
            if server.is_remote or server.status not in [Status.RUNNING, Status.PAUSED]:
                continue
            if not server.process:
                self.log.warning(f"DCSServerBot is not attached to a DCS.exe or DCS_Server.exe process on "
                                 f"server {server.name}, skipping server load gathering.")
                continue
            try:
                cpu = server.process.cpu_percent()
                memory = server.process.memory_full_info()
                io_counters = server.process.io_counters()
                if server.process.pid not in self.io_counters:
                    write_bytes = read_bytes = 0
                else:
                    write_bytes = io_counters.write_bytes - self.io_counters[server.process.pid].write_bytes
                    read_bytes = io_counters.read_bytes - self.io_counters[server.process.pid].read_bytes
                self.io_counters[server.process.pid] = io_counters
                net_io_counters = psutil.net_io_counters(pernic=False)
                if not self.net_io_counters:
                    bytes_sent = bytes_recv = 0
                else:
                    bytes_sent = int((net_io_counters.bytes_sent - self.net_io_counters.bytes_sent) / 7200)
                    bytes_recv = int((net_io_counters.bytes_recv - self.net_io_counters.bytes_recv) / 7200)
                self.net_io_counters = net_io_counters
                self.bus.send_to_node({
                    "command": "serverLoad",
                    "cpu": cpu,
                    "mem_total": memory.vms,
                    "mem_ram": memory.rss,
                    "read_bytes": read_bytes,
                    "write_bytes": write_bytes,
                    "bytes_recv": bytes_recv,
                    "bytes_sent": bytes_sent,
                    "server_name": server.name
                })
            except (psutil.AccessDenied, PermissionError):
                self.log.debug(f"Server {server.name} was not started by the bot, skipping server load gathering.")

    @tasks.loop(minutes=1.0)
    async def monitoring(self):
        try:
            if self.node.master:
                await self.check_nodes()
            if sys.platform == 'win32':
                await self.check_popups()
            await self.heartbeat()
            if 'serverstats' in self.node.config.get('opt_plugins', []):
                await self.serverload()
        except Exception as ex:
            self.log.exception(ex)

    @monitoring.before_loop
    async def before_loop(self):
        if self.node.master:
            bot: DCSServerBot = ServiceRegistry.get("Bot").bot
            await bot.wait_until_ready()
