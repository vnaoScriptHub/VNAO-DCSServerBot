import asyncio
import atexit
import os
from datetime import datetime, timezone, timedelta

import discord

from core import Server, ServiceRegistry, Node, PersistentReport, Report, Status, Coalition
from services.bot import BotService
from services.servicebus import ServiceBus
from typing import Optional, Union


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
        server.maintenance = False
    elif reboot:
        bus = ServiceRegistry.get(ServiceBus)
        for server in [x for x in bus.servers.values() if x.status not in [Status.SHUTDOWN, Status.UNREGISTERED]]:
            if not server.is_remote:
                await bus.send_to_node({"command": "onShutdown", "server_name": server.name})
                await asyncio.sleep(1)
                await server.shutdown()
        atexit.register(_reboot)
        await node.shutdown()


async def halt(node: Node):
    def _halt():
        os.system("shutdown /s /t 1")

    bus = ServiceRegistry.get(ServiceBus)
    for server in [x for x in bus.servers.values() if x.status not in [Status.SHUTDOWN, Status.UNREGISTERED]]:
        if not server.is_remote:
            await bus.send_to_node({"command": "onShutdown", "server_name": server.name})
            await asyncio.sleep(1)
            await server.shutdown()
    atexit.register(_halt)
    await node.shutdown()


async def cmd(node: Node, cmd: str):
    out, err = await node.shell_command(cmd)
    if err:
        node.log.error(err)
    else:
        node.log.info(out)


async def popup(node: Node, server: Server, message: str, to: Optional[str] = 'all', timeout: Optional[int] = 10):
    await server.sendPopupMessage(Coalition(to), message, timeout)


async def purge_channel(node: Node, channel: Union[int, list[int]], delete_after: int = 0, ignore: int = None):
    if not node.master:
        return
    bot = ServiceRegistry.get(BotService).bot
    now = datetime.now(tz=timezone.utc)
    threshold_time = now - timedelta(days=delete_after)

    if isinstance(channel, int):
        channels = [channel]
    else:
        channels = channel
    for c in channels:
        channel = bot.get_channel(c)
        if not channel:
            node.log.warning(f"Channel {c} not found!")
            return

        try:
            def check(message: discord.Message):
                return not ignore or message.author.id != ignore

            # Bulk delete messages that are less than 14 days old and match the criteria
            node.log.debug(f"Deleting messages older than {delete_after} days in channel {channel.name} ...")
            deleted_messages = await channel.purge(limit=None, before=threshold_time, check=check, bulk=True)
            node.log.debug(f"Purged {len(deleted_messages)} messages from channel {channel.name}.")
        except discord.NotFound:
            node.log.warning(f"Can't delete messages in channel {channel.name}: Not found")
        except discord.Forbidden:
            node.log.warning(f"Can't delete messages in channel {channel.name}: Missing permissions")
        except discord.HTTPException:
            node.log.error(f"Failed to delete message in channel {channel.name}", exc_info=True)
