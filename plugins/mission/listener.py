from __future__ import annotations
import asyncio
import discord
import os
import shlex

from contextlib import closing
from core import utils, EventListener, PersistentReport, Plugin, Report, Status, Side, Mission, Player, Coalition, \
    Channel, DataObjectFactory, event, chat_command, ServiceRegistry
from datetime import datetime, timezone
from discord.ext import tasks
from psycopg.rows import dict_row
from queue import Queue
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core import Server


class MissionEventListener(EventListener):
    EVENT_TEXTS = {
        Side.BLUE: {
            'takeoff': '```ansi\n\u001b[0;34mBLUE player {} took off from {}.```',
            'landing': '```ansi\n\u001b[0;34mBLUE player {} landed at {}.```',
            'eject': '```ansi\n\u001b[0;34mBLUE player {} ejected.```',
            'crash': '```ansi\n\u001b[0;34mBLUE player {} crashed.```',
            'pilot_death': '```ansi\n\u001b[0;34mBLUE player {} died.```',
            'kill': '```ansi\n\u001b[0;34mBLUE {} in {} killed {} {} in {} with {}.```',
            'friendly_fire': '```ansi\n\u001b[1;33mBLUE {} FRIENDLY FIRE onto {} with {}.```',
            'self_kill': '```ansi\n\u001b[0;34mBLUE player {} killed themselves - Ooopsie!```',
            'change_slot': '```ansi\n\u001b[0;34m{} player {} occupied {} {}```',
            'disconnect': '```ansi\n\u001b[0;34mBLUE player {} disconnected```'
        },
        Side.RED: {
            'takeoff': '```ansi\n\u001b[0;31mRED player {} took off from {}.```',
            'landing': '```ansi\n\u001b[0;31mRED player {} landed at {}.```',
            'eject': '```ansi\n\u001b[0;31mRED player {} ejected.```',
            'crash': '```ansi\n\u001b[0;31mRED player {} crashed.```',
            'pilot_death': '```ansi\n\u001b[0;31mRED player {} died.```',
            'kill': '```ansi\n\u001b[0;31mRED {} in {} killed {} {} in {} with {}.```',
            'friendly_fire': '```ansi\n\u001b[1;33mRED {} FRIENDLY FIRE onto {} with {}.```',
            'self_kill': '```ansi\n\u001b[0;31mRED player {} killed themselves - Ooopsie!```',
            'change_slot': '```ansi\n\u001b[0;31m{} player {} occupied {} {}```',
            'disconnect': '```ansi\n\u001b[0;31mRED player {} disconnected```'
        },
        Side.NEUTRAL: {
            'takeoff': '```ansi\n\u001b[0;32mNEUTRAL player {} took off from {}.```',
            'landing': '```ansi\n\u001b[0;32mNEUTRAL player {} landed at {}.```',
            'eject': '```ansi\n\u001b[0;32mNEUTRAL player {} ejected.```',
            'crash': '```ansi\n\u001b[0;32mNEUTRAL player {} crashed.```',
            'pilot_death': '```ansi\n\u001b[0;32mNEUTRAL player {} died.```',
            'kill': '```ansi\n\u001b[0;32mNEUTRAL {} in {} killed {} {} in {} with {}.```',
            'friendly_fire': '```ansi\n\u001b[1;33mNEUTRAL {} FRIENDLY FIRE onto {} with {}.```',
            'self_kill': '```ansi\n\u001b[0;32mNEUTRAL player {} killed themselves - Ooopsie!```',
            'change_slot': '```ansi\n\u001b[0;32m{} player {} occupied {} {}```',
            'disconnect': '```ansi\n\u001b[0;32mNEUTRAL player {} disconnected```'
        },
        Side.SPECTATOR: {
            'connect': '```\nPlayer {} connected to server```',
            'disconnect': '```\nPlayer {} disconnected```',
            'spectators': '```\n{} player {} returned to Spectators```',
            'takeoff': '```\nPlayer {} took off from {}.```',
            'landing': '```\nPlayer {} landed at {}.```',
            'crash': '```\nPlayer {} crashed.```',
            'eject': '```\nPlayer {} ejected.```',
            'pilot_death': '```\n[Player {} died.```',
            'kill': '```\n{} in {} killed {} {} in {} with {}.```',
            'friendly_fire': '```ansi\n\u001b[1;33m{} FRIENDLY FIRE onto {} with {}.```'
        },
        Side.UNKNOWN: {
            'takeoff': '```\n{} took off from {}.```',
            'landing': '```\n{} landed at {}.```',
            'eject': '```\n{} ejected.```',
            'crash': '```\n{} crashed.```',
            'pilot_death': '```\n{} died.```',
            'kill': '```\n{} in {} killed {} {} in {} with {}.```',
            'friendly_fire': '```ansi\n\u001b[1;33m{} FRIENDLY FIRE onto {} with {}.```',
            'self_kill': '```\n{} killed themselves - Ooopsie!```'
        }
    }

    def __init__(self, plugin: Plugin):
        super().__init__(plugin)
        self.queue: dict[int, Queue[str]] = dict()
        self.player_embeds: dict[str, bool] = dict()
        self.mission_embeds: dict[str, bool] = dict()
        self.print_queue.start()
        self.update_player_embed.start()
        self.update_mission_embed.start()

    async def shutdown(self):
        self.print_queue.cancel()
        await self.work_queue()
        self.update_player_embed.cancel()
        self.update_mission_embed.cancel()

    async def work_queue(self):
        for channel in list(self.queue.keys()):
            if self.queue[channel].empty():
                continue
            _channel = self.bot.get_channel(channel)
            if not _channel:
                _channel = await self.bot.fetch_channel(channel)
                if not _channel:
                    return
            messages = message_old = ''
            while not self.queue[channel].empty():
                message = self.queue[channel].get()
                if message != message_old:
                    if len(messages + message) > 2000:
                        await _channel.send(messages)
                        messages = message
                    else:
                        messages += message
                    message_old = message
            if messages:
                await _channel.send(messages)

    @tasks.loop(seconds=2)
    async def print_queue(self):
        try:
            await self.work_queue()
            if self.print_queue.seconds == 10:
                self.print_queue.change_interval(seconds=2)
        except discord.DiscordException as ex:
            self.log.exception(ex)
            self.print_queue.change_interval(seconds=10)
        except Exception as ex:
            self.log.debug("Exception in print_queue(): " + str(ex))

    @tasks.loop(seconds=5)
    async def update_player_embed(self):
        for server_name, update in self.player_embeds.copy().items():
            if update:
                try:
                    server = self.bot.servers.get(server_name)
                    if server and not server.locals.get('coalitions'):
                        report = PersistentReport(self.bot, self.plugin_name, 'players.json',
                                                  embed_name='players_embed', server=server)
                        await report.render(server=server, sides=[Coalition.BLUE, Coalition.RED])
                except Exception as ex:
                    self.log.exception(ex)
                finally:
                    self.player_embeds[server_name] = False

    @tasks.loop(seconds=5)
    async def update_mission_embed(self):
        for server_name, update in self.mission_embeds.copy().items():
            if update:
                try:
                    server = self.bot.servers.get(server_name)
                    if not server or not server.settings:
                        continue
                    report = PersistentReport(self.bot, self.plugin_name, 'serverStatus.json',
                                              embed_name='mission_embed', server=server)
                    await report.render(server=server)
                except Exception as ex:
                    self.log.exception(ex)
                finally:
                    self.mission_embeds[server_name] = False

    @print_queue.before_loop
    async def before_check(self):
        await self.bot.wait_until_ready()

    @event(name="sendMessage")
    async def sendMessage(self, server: Server, data: dict) -> None:
        channel_id = int(data['channel'])
        if channel_id == -1:
            channel_id = server.channels[Channel.EVENTS]
        channel = self.bot.get_channel(channel_id)
        if channel:
            await channel.send("```" + data['message'] + "```")

    @event(name="sendEmbed")
    async def sendEmbed(self, server: Server, data: dict) -> None:
        embed = utils.format_embed(data)
        if 'id' in data and len(data['id']) > 0:
            channel = int(data['channel'])
            if channel == -1:
                channel = Channel.STATUS
            await self.bot.setEmbed(embed_name=data['id'], embed=embed, channel_id=channel, server=server)
        else:
            channel_id = int(data['channel'])
            if channel_id == -1:
                channel_id = server.channels[Channel.EVENTS]
            channel = self.bot.get_channel(channel_id)
            if channel:
                await channel.send(embed=embed)

    def send_dcs_event(self, server: Server, side: Side, message: str) -> None:
        events_channel = None
        if server.locals.get('coalitions'):
            if side == Side.RED:
                events_channel = server.channels.get(Channel.COALITION_RED_EVENTS, -1)
            elif side == Side.BLUE:
                events_channel = server.channels.get(Channel.COALITION_BLUE_EVENTS, -1)
        if not events_channel:
            events_channel = server.channels.get(Channel.EVENTS, -1)
        if int(events_channel) != -1:
            if events_channel not in self.queue:
                self.queue[events_channel] = Queue()
            self.queue[events_channel].put(message)

    def display_mission_embed(self, server: Server):
        self.mission_embeds[server.name] = True

    # Display the list of active players
    def display_player_embed(self, server: Server):
        self.player_embeds[server.name] = True

    @event(name="callback")
    async def callback(self, server: Server, data: dict):
        if data['subcommand'] in ['startMission', 'restartMission', 'pause', 'shutdown']:
            data['command'] = data['subcommand']
            server.send_to_dcs(data)

    @staticmethod
    def _update_mission(server: Server, data: dict) -> None:
        if not server.current_mission:
            server.current_mission = DataObjectFactory().new(
                Mission.__name__, node=server.node, server=server, map=data['current_map'],
                name=data['current_mission'])
        server.current_mission.update(data)

    def _update_bans(self, server: Server):
        def _get_until(until: datetime) -> str:
            if until.year == 9999:
                return 'never'
            else:
                return until.strftime('%Y-%m-%d %H:%M') + ' (UTC)'

        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                server.send_to_dcs({
                   "command": "ban",
                   "batch": [
                       {
                           "ucid": ban['ucid'],
                           "reason": ban['reason'],
                           "banned_until": _get_until(ban['banned_until'])
                       }
                       for ban in cursor.execute(
                           'SELECT ucid, reason, banned_until FROM bans WHERE banned_until >= NOW()'
                       )
                    ]
                })

    async def _watchlist_alert(self, server: Server, player: Player):
        mentions = ''.join([self.bot.get_role(role).mention for role in self.bot.roles['DCS Admin']])
        embed = discord.Embed(title='Watchlist member joined!', colour=discord.Color.red())
        embed.description = "A user just joined that you put on the watchlist."
        embed.add_field(name="Server", value=server.name, inline=False)
        embed.add_field(name="Player", value=player.name)
        embed.add_field(name="UCID", value=player.ucid)
        if player.member:
            embed.add_field(name="_ _", value='_ _')
            embed.add_field(name="Member", value=player.member.display_name)
            embed.add_field(name="Discord ID", value=player.member.id)
            embed.add_field(name="_ _", value='_ _')
        embed.set_footer(text="Players can be removed from the watchlist by using the /info command.")
        await self.bot.get_admin_channel(server).send(mentions, embed=embed)

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        # the server is starting up
        # if not data['channel'].startswith('sync-'):
        #    return
        self._update_bans(server)
        if 'current_mission' not in data:
            server.status = Status.STOPPED
            return
        self._update_mission(server, data)
        if 'players' not in data:
            server.players.clear()
            data['players'] = []
            server.status = Status.STOPPED
        elif data['channel'].startswith('sync-'):
            server.status = Status.PAUSED if data['pause'] is True else Status.RUNNING
        server.afk.clear()
        # all players are inactive for now
        for p in server.players.values():
            p.active = False
        for p in data['players']:
            if p['id'] == 1:
                continue
            player: Player = server.get_player(ucid=p['ucid'])
            if not player:
                player: Player = DataObjectFactory().new(
                    Player.__name__, node=server.node, server=server, id=p['id'], name=p['name'], active=p['active'],
                    side=Side(p['side']), ucid=p['ucid'], slot=int(p['slot']), sub_slot=p['sub_slot'],
                    unit_callsign=p['unit_callsign'], unit_name=p['unit_name'], unit_type=p['unit_type'],
                    unit_display_name=p.get('unit_display_name', p['unit_type']), group_id=p['group_id'],
                    group_name=p['group_name'])
                server.add_player(player)
            else:
                player.update(p)
            if Side(p['side']) == Side.SPECTATOR:
                server.afk[player.ucid] = datetime.now(timezone.utc)
        # cleanup inactive players
        for p in list(server.players.values()):
            if not p.active and not p.id == 1:
                del server.players[p.id]
        self.display_mission_embed(server)
        self.display_player_embed(server)

    @event(name="onMissionLoadBegin")
    async def onMissionLoadBegin(self, server: Server, data: dict) -> None:
        server.status = Status.LOADING
        self._update_mission(server, data)
        if server.settings:
            self.display_mission_embed(server)
        self.display_player_embed(server)

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, data: dict) -> None:
        self._update_mission(server, data)
        self.display_mission_embed(server)

    @event(name="onSimulationStart")
    async def onSimulationStart(self, server: Server, data: dict) -> None:
        server.status = Status.PAUSED
        self.display_mission_embed(server)

    @event(name="getMissionUpdate")
    async def getMissionUpdate(self, server: Server, data: dict) -> None:
        if not server.current_mission:
            server.status = Status.STOPPED
            return
        elif data['pause'] and server.status == Status.RUNNING:
            server.status = Status.PAUSED
        elif not data['pause'] and server.status != Status.RUNNING:
            server.status = Status.RUNNING
        server.current_mission.mission_time = data['mission_time']
        server.current_mission.real_time = data['real_time']
        self.display_mission_embed(server)

    @event(name="onSimulationStop")
    async def onSimulationStop(self, server: Server, data: dict) -> None:
        server.status = Status.STOPPED
        for p in server.get_active_players():
            p.side = Side.SPECTATOR
        self.display_mission_embed(server)
        self.display_player_embed(server)

    @event(name="onSimulationPause")
    async def onSimulationPause(self, server: Server, data: dict) -> None:
        server.status = Status.PAUSED
        self.display_mission_embed(server)

    @event(name="onSimulationResume")
    async def onSimulationResume(self, server: Server, data: dict) -> None:
        server.status = Status.RUNNING
        self.display_mission_embed(server)

    @event(name="onPlayerConnect")
    async def onPlayerConnect(self, server: Server, data: dict) -> None:
        if data['id'] == 1:
            return
        self.send_dcs_event(server, Side.SPECTATOR, self.EVENT_TEXTS[Side.SPECTATOR]['connect'].format(data['name']))
        player: Player = server.get_player(ucid=data['ucid'])
        if not player or player.id == 1:
            player: Player = DataObjectFactory().new(
                Player.__name__, node=server.node, server=server, id=data['id'], name=data['name'],
                active=data['active'], side=Side(data['side']), ucid=data['ucid'])
            server.add_player(player)
        else:
            player.update(data)
        if player.member:
            server.send_to_dcs({
                'command': 'uploadUserRoles',
                'id': player.id,
                'ucid': player.ucid,
                'roles': [x.id for x in player.member.roles]
            })
        if player.watchlist:
            await self._watchlist_alert(server, player)

    @event(name="onPlayerStart")
    async def onPlayerStart(self, server: Server, data: dict) -> None:
        if data['id'] == 1 or 'ucid' not in data:
            return
        player: Player = server.get_player(id=data['id'])
        if not player:
            player = DataObjectFactory().new(
                Player.__name__, node=server.node, server=server, id=data['id'], name=data['name'],
                active=data['active'], side=Side(data['side']), ucid=data['ucid'])
            server.add_player(player)
        else:
            player.update(data)
        # add the player to the afk list
        server.afk[player.ucid] = datetime.now(timezone.utc)
        self.display_mission_embed(server)
        self.display_player_embed(server)

    @event(name="onPlayerStop")
    async def onPlayerStop(self, server: Server, data: dict) -> None:
        if data['id'] == 1:
            return
        player: Player = server.get_player(id=data['id'])
        if player:
            player.active = False
            if player.ucid in server.afk:
                del server.afk[player.ucid]
        self.display_mission_embed(server)
        self.display_player_embed(server)

    def _disconnect(self, server: Server, player: Player):
        if not player or not player.active:
            return
        try:
            self.send_dcs_event(server, player.side,
                                self.EVENT_TEXTS[player.side]['disconnect'].format(player.name))
        finally:
            player.active = False
            if player.ucid in server.afk:
                del server.afk[player.ucid]
            self.display_mission_embed(server)
            self.display_player_embed(server)

    @event(name="onPlayerChangeSlot")
    async def onPlayerChangeSlot(self, server: Server, data: dict) -> None:
        player: Player = server.get_player(id=data['id'], active=True)
        if not player:
            return
        # Workaround for missing disconnect events
        if 'side' not in data:
            self._disconnect(server, player)
            return
        try:
            if Side(data['side']) != Side.SPECTATOR:
                if player.ucid in server.afk:
                    del server.afk[player.ucid]
                side = Side(data['side'])
                self.send_dcs_event(server, side, self.EVENT_TEXTS[side]['change_slot'].format(
                    player.side.name if player.side != Side.SPECTATOR else 'NEUTRAL',
                    data['name'], Side(data['side']).name, data['unit_type']))
            else:
                server.afk[player.ucid] = datetime.now(timezone.utc)
                self.send_dcs_event(server, Side.SPECTATOR,
                                    self.EVENT_TEXTS[Side.SPECTATOR]['spectators'].format(player.side.name,
                                                                                          data['name']))
        finally:
            if player:
                player.update(data)
            self.display_player_embed(server)

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        # ignore game events until the server is not initialized correctly
        if server.status not in [Status.RUNNING, Status.STOPPED]:
            return
        if data['eventName'] in ['mission_end', 'connect', 'change_slot']:  # these events are handled differently
            return
        elif data['eventName'] == 'disconnect':
            if data['arg1'] == 1:
                return
            self._disconnect(server, server.get_player(id=data['arg1'], active=True))
        elif data['eventName'] == 'friendly_fire' and data['arg1'] != data['arg3']:
            player1 = server.get_player(id=data['arg1'])
            player2 = server.get_player(id=data['arg3'])
            # TODO: remove if issue with Forrestal is fixed
            if not player2:
                return
            # filter AI-only events
            if not player1 and not server.locals.get('display_ai_chat', False):
                return
            side = player1.side if player1 else player2.side if player2 else Side.UNKNOWN
            self.send_dcs_event(server, side, self.EVENT_TEXTS[side][data['eventName']].format(
                ('player ' + player1.name) if player1 else 'AI',
                ('player ' + player2.name) if player2 else 'AI',
                data['arg2'] or 'Cannon/Bomblet')
            )
        elif data['eventName'] == 'self_kill':
            player = server.get_player(id=data['arg1']) if data['arg1'] != -1 else None
            side = player.side if player else Side.UNKNOWN
            if player or server.locals.get('display_ai_chat', False):
                self.send_dcs_event(server, side,
                                    self.EVENT_TEXTS[side][data['eventName']].format(player.name if player else 'AI'))
        elif data['eventName'] == 'kill':
            player1 = server.get_player(id=data['arg1'])
            player2 = server.get_player(id=data['arg4'])
            # filter AI-only events
            if not player1 and not player2 and not server.locals.get('display_ai_chat', False):
                return
            side = Side(data['arg3'])
            self.send_dcs_event(server, side, self.EVENT_TEXTS[side][data['eventName']].format(
                ('player ' + player1.name) if player1 is not None else 'AI',
                data['arg2'] or 'SCENERY', Side(data['arg6']).name,
                ('player ' + player2.name) if player2 is not None else 'AI',
                data['arg5'] or 'SCENERY', data['arg7'] or 'Cannon/Bomblet'))
            # report teamkills from players to admins (only on public servers)
            if server.is_public() and player1 and player2 and data['arg1'] != data['arg4'] \
                    and data['arg3'] == data['arg6']:
                name = ('Member ' + player1.member.display_name) if player1.member else ('Player ' + player1.display_name)
                message = f"{name} (ucid={player1.ucid}) is killing team members. Please investigate."
                # show the server name on central admin channels
                if self.bot.locals.get('admin_channel'):
                    message = f"{server.display_name}: " + message
                await self.bot.get_admin_channel(server).send(message)
        elif data['eventName'] in ['takeoff', 'landing', 'crash', 'eject', 'pilot_death']:
            player = server.get_player(id=data['arg1'])
            side = player.side if player else Side.UNKNOWN
            if not player and not server.locals.get('display_ai_chat', False):
                return
            if data['eventName'] in ['takeoff', 'landing']:
                self.send_dcs_event(server, side, self.EVENT_TEXTS[side][data['eventName']].format(
                    player.name if player else 'AI', data['arg3'] if len(data['arg3']) > 0 else 'ground')
                )
            else:
                self.send_dcs_event(server, side, self.EVENT_TEXTS[side][data['eventName']].format(
                    player.name if player else 'AI')
                )

    @chat_command(name="atis", usage="<airport>", help="display ATIS information")
    async def atis(self, server: Server, player: Player, params: list[str]):
        if len(params) == 0:
            player.sendChatMessage(f"Usage: -atis <airbase/code>")
            return
        name = ' '.join(params)
        for airbase in server.current_mission.airbases:
            if (name.casefold() in airbase['name'].casefold()) or (name.upper() == airbase['code']):
                response = await server.send_to_dcs_sync({
                    "command": "getWeatherInfo",
                    "x": airbase['position']['x'],
                    "y": airbase['position']['y'],
                    "z": airbase['position']['z']
                })
                report = Report(self.bot, self.plugin_name, 'atis-ingame.json')
                env = await report.render(airbase=airbase, data=response)
                message = utils.embed_to_simpletext(env.embed)
                player.sendUserMessage(message, 30)
                return
        player.sendChatMessage(f"No ATIS information found for {name}.")

    @chat_command(name="restart", roles=['DCS Admin'], usage="[time]", help="restart the running mission")
    async def restart(self, server: Server, player: Player, params: list[str]):
        delay = int(params[0]) if len(params) > 0 else 0
        if delay > 0:
            message = f'!!! Server will be restarted in {utils.format_time(delay)}!!!'
        else:
            message = '!!! Server will be restarted NOW !!!'
        server.sendPopupMessage(Coalition.ALL, message)
        self.bot.loop.call_soon(asyncio.create_task, server.current_mission.restart())

    @chat_command(name="list", roles=['DCS Admin'], help="lists available missions")
    async def _list(self, server: Server, player: Player, params: list[str]):
        missions = await server.getMissionList()
        message = 'The following missions are available:\n'
        for i in range(0, len(missions)):
            mission = missions[i]
            mission = mission[(mission.rfind(os.path.sep) + 1):-4]
            message += f"{i + 1} {mission}\n"
        message += f"\nUse {self.prefix}load <number> to load that mission"
        player.sendUserMessage(message, 30)

    @chat_command(name="load", roles=['DCS Admin'], usage="<number>", help="load a specific mission")
    async def load(self, server: Server, player: Player, params: list[str]):
        self.bot.loop.call_soon(asyncio.create_task, server.loadMission(int(params[0])))

    @chat_command(name="ban", roles=['DCS Admin'], usage="<name> [reason]", help="ban a user for 3 days")
    async def ban(self, server: Server, player: Player, params: list[str]):
        await self._handle_command(server, player, params, lambda delinquent, reason: (
            ServiceRegistry.get('ServiceBus').ban(delinquent.ucid, player.member.display_name, reason, 3),
            f'User {delinquent.display_name} banned for 3 days'))

    @chat_command(name="kick", roles=['DCS Admin'], usage="<name> [reason]", help="kick a user")
    async def kick(self, server: Server, player: Player, params: list[str]):
        await self._handle_command(server, player, params, lambda delinquent, reason: (
            server.kick(delinquent, reason),
            f'User {delinquent.display_name} kicked'))

    @chat_command(name="spec", roles=['DCS Admin'], usage="<name> [reason]", help="moves a user to spectators")
    async def spec(self, server: Server, player: Player, params: list[str]):
        await self._handle_command(server, player, params, lambda delinquent, reason: (
            server.move_to_spectators(delinquent, reason),
            f'User {delinquent.display_name} moved to spectators'))

    async def _handle_command(self, server: Server, player: Player, params: list[str], action):
        if not params:
            player.sendChatMessage(
                f"Usage: {self.prefix}{action.__name__} <name> [reason]")
            return

        params = shlex.split(' '.join(params))
        name = params[0]
        reason = ' '.join(params[1:]) if len(params) > 1 else 'n/a'

        delinquent: Player = server.get_player(name=name, active=True)
        if not delinquent:
            player.sendChatMessage(f'Player {name} not found. Use "" around names with blanks.')
            return

        action_result, audit_msg = action(delinquent, reason)
        action_description = ' '.join(audit_msg.split()[2:])

        player.sendChatMessage(audit_msg)
        await self.bot.audit(f'Player {delinquent.display_name} {action_description}' +
                             (f' with reason "{reason}".' if reason != 'n/a' else '.'),
                             user=player.member)

    @chat_command(name="911", usage="<message>", help="send an alert to admins (misuse will be punished!)")
    async def call911(self, server: Server, player: Player, params: list[str]):
        mentions = ''.join([self.bot.get_role(role).mention for role in self.bot.roles['DCS Admin']])
        message = ' '.join(params)
        embed = discord.Embed(title='MAYDAY // 911 Call', colour=discord.Color.blue())
        embed.set_image(url="https://media.tenor.com/pDRfpNAXfmcAAAAC/despicable-me-minions.gif")
        embed.description = message
        embed.add_field(name="Server", value=server.name, inline=False)
        embed.add_field(name="Player", value=player.name)
        embed.add_field(name="UCID", value=player.ucid)
        await self.bot.get_admin_channel(server).send(mentions, embed=embed)

    @chat_command(name="preset", aliases=["presets"], roles=['DCS Admin'], usage="<preset>",
                  help="load a specific weather preset")
    async def preset(self, server: Server, player: Player, params: list[str]):
        async def change_preset(preset: str):
            filename = await server.get_current_mission_file()
            if not server.node.config.get('mission_rewrite', True):
                await server.stop()
            new_filename = await server.modifyMission(filename, utils.get_preset(preset))
            if new_filename != filename:
                await server.replaceMission(int(server.settings['listStartIndex']), new_filename)
            await server.restart(modify_mission=False)
            if server.status == Status.STOPPED:
                await server.start()
            await self.bot.audit(f"changed preset to {preset}", server=server, user=player.ucid)

        presets = list(utils.get_presets())
        if presets:
            if not params:
                message = 'The following presets are available:\n'
                for idx, preset in enumerate(presets):
                    message += f"{idx + 1} {preset}\n"
                message += f"\nUse {self.prefix}preset <number> to load that preset " \
                           f"(mission will be restarted!)"
                player.sendUserMessage(message, 30)
            else:
                n = int(params[0]) - 1
                self.bot.loop.call_soon(asyncio.create_task, change_preset(presets[n]))
        else:
            player.sendChatMessage(f"There are no presets available to select.")
