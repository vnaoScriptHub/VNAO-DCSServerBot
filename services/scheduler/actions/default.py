import asyncio
import atexit
import os

from core import Server, ServiceRegistry, Node, PersistentReport, Report, Status
from services.bot import BotService
from services.servicebus import ServiceBus
from typing import Optional


async def report(file: str, channel: int, node: Node, persistent: Optional[bool] = True,
                 server: Optional[Server] = None):
    # we can only render on the master node
    if not node.master:
        return
    bot = ServiceRegistry.get(BotService).bot
    if bot.is_closed():
        return
    if persistent:
        r = PersistentReport(bot, 'scheduler', file, channel_id=channel, server=server,
                             embed_name=os.path.basename(file)[:-5])
        await r.render(node=node, server=server)
    else:
        r = Report(bot, 'scheduler', file)
        env = await r.render(node=node, server=server)
        await bot.get_channel(channel).send(embed=env.embed)


async def restart(node: Node, server: Optional[Server] = None, shutdown: Optional[bool] = False,
                  rotate: Optional[bool] = False, run_extensions: Optional[bool] = True,
                  reboot: Optional[bool] = False):
    def _reboot():
        os.system("shutdown /r /t 1")

    if server and server.status not in [Status.SHUTDOWN, Status.UNREGISTERED]:
        server.maintenance = True
        if shutdown:
            await ServiceRegistry.get(ServiceBus).send_to_node({"command": "onShutdown", "server_name": server.name})
            await asyncio.sleep(1)
            await server.shutdown()
            await server.startup()
        elif rotate:
            await server.loadNextMission(modify_mission=run_extensions)
        else:
            await server.restart(modify_mission=run_extensions)
    elif reboot:
        bus = ServiceRegistry.get(ServiceBus)
        for server in [x for x in bus.servers.values() if x.status not in [Status.SHUTDOWN, Status.UNREGISTERED]]:
            if not server.is_remote:
                await bus.send_to_node({"command": "onShutdown", "server_name": server.name})
                await asyncio.sleep(1)
                await server.shutdown()
        atexit.register(_reboot)
        await node.shutdown()


async def cmd(node: Node, cmd: str):
    out, err = await node.shell_command(cmd)
    if err:
        node.log.info(err)
    else:
        node.log.info(out)
