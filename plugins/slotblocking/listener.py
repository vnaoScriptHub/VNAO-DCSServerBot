import re
import discord

from core import EventListener, Plugin, Server, Side, Status, utils, event
from typing import Union, cast
from plugins.creditsystem.player import CreditPlayer


class SlotBlockingListener(EventListener):
    def __init__(self, plugin: Plugin):
        super().__init__(plugin)

    def load_params_into_mission(self, server: Server):
        config: dict = self.plugin.get_config(server, use_cache=False)
        if config:
            server.send_to_dcs({
                'command': 'loadParams',
                'plugin': self.plugin_name,
                'params': config
            })
            guild = self.bot.guilds[0]
            roles = []
            for role in config.get('VIP', {}).get('discord', []):
                roles.append(self.bot.get_role(role))
            if not roles:
                return
            # get all linked members
            with self.pool.connection() as conn:
                for row in conn.execute("""
                    SELECT ucid, discord_id FROM players WHERE discord_id != -1 AND LENGTH(ucid) = 32
                """).fetchall():
                    member = guild.get_member(row[1])
                    if not member:
                        continue
                    for role in member.roles:
                        if role in roles:
                            server.send_to_dcs({
                                'command': 'uploadUserRoles',
                                'ucid': row[0],
                                'roles': [x.name for x in member.roles]
                            })
                            break

    @event(name="registerDCSServer")
    async def registerDCSServer(self, server: Server, data: dict) -> None:
        # the server is running already
        if data['channel'].startswith('sync-'):
            self.load_params_into_mission(server)

    @event(name="onMissionLoadEnd")
    async def onMissionLoadEnd(self, server: Server, data: dict) -> None:
        self.load_params_into_mission(server)

    def _get_points(self, server: Server, player: CreditPlayer) -> int:
        config = self.plugin.get_config(server)
        if 'restricted' in config:
            for unit in config['restricted']:
                if ('unit_type' in unit and unit['unit_type'] == player.unit_type) or \
                        ('unit_name' in unit and unit['unit_name'] in player.unit_name) or \
                        ('group_name' in unit and unit['group_name'] in player.group_name):
                    if player.sub_slot == 0 and 'points' in unit:
                        return unit['points']
                    elif player.sub_slot > 0 and 'crew' in unit:
                        return unit['crew']
        return 0

    def _get_costs(self, server: Server, data: Union[CreditPlayer, dict]) -> int:
        config = self.plugin.get_config(server)
        unit_type = data.unit_type if isinstance(data, CreditPlayer) else data['unit_type']
        unit_name = data.unit_name if isinstance(data, CreditPlayer) else data['unit_name']
        group_name = data.group_name if isinstance(data, CreditPlayer) else data['group_name']
        if 'restricted' in config:
            for unit in config['restricted']:
                if ('unit_type' in unit and re.match(unit['unit_type'], unit_type)) or \
                        ('unit_name' in unit and re.match(unit['unit_name'], unit_name)) or \
                        ('group_name' in unit and re.match(unit['group_name'], group_name)):
                    if 'costs' in unit:
                        return unit['costs']
        return 0

    def _is_vip(self, config: dict, data: dict) -> bool:
        if 'VIP' not in config:
            return False
        if 'ucid' in config['VIP']:
            ucid = config['VIP']['ucid']
            if (isinstance(ucid, str) and ucid == data['ucid']) or (isinstance(ucid, list) and data['ucid'] in ucid):
                return True
        if 'discord' in config['VIP']:
            member = self.bot.get_member_by_ucid(data['ucid'])
            return utils.check_roles(config['VIP']['discord'], member) if member else False
        return False

    @event(name="onPlayerConnect")
    async def onPlayerConnect(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if not config or data['id'] == 1:
            return
        if self._is_vip(config, data) and 'audit' in config['VIP'] and config['VIP']['audit']:
            member = self.bot.get_member_by_ucid(data['ucid'])
            if member:
                message = "VIP member {} joined".format(utils.escape_string(member.display_name))
            else:
                message = "VIP user {}(ucid={} joined".format(utils.escape_string(data['name']), data['ucid'])
            await self.bot.audit(message, server=server)

    @event(name="onPlayerChangeSlot")
    async def onPlayerChangeSlot(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if not config:
            return
        if 'side' in data and 'use_reservations' in config and config['use_reservations']:
            player: CreditPlayer = cast(CreditPlayer, server.get_player(ucid=data['ucid'], active=True))
            if player and player.deposit > 0:
                old_points = player.points
                player.points -= player.deposit
                player.audit('buy', old_points, 'Points taken for using a reserved module')
                player.deposit = 0
            # if mission statistics are enabled, use BIRTH events instead
            if player and not self.get_config(server, plugin_name='missionstats').get('enabled', True) and \
                    Side(data['side']) != Side.SPECTATOR:
                # only pilots have to "pay" for their plane
                if int(data['sub_slot']) == 0:
                    player.deposit = self._get_costs(server, data)

    @event(name="onMissionEvent")
    async def onMissionEvent(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if not config:
            return
        if data['eventName'] == 'S_EVENT_BIRTH':
            initiator = data['initiator']
            # check, if they are a human player
            if 'name' not in initiator:
                return
            if 'use_reservations' in config and config['use_reservations']:
                player: CreditPlayer = cast(CreditPlayer, server.get_player(name=initiator['name'], active=True))
                # only pilots have to "pay" for their plane
                if player and player.sub_slot == 0:
                    player.deposit = self._get_costs(server, player)

    @event(name="onGameEvent")
    async def onGameEvent(self, server: Server, data: dict) -> None:
        config = self.plugin.get_config(server)
        if not config or 'restricted' not in config or server.status != Status.RUNNING:
            return
        if data['eventName'] == 'kill':
            # players only lose points if they weren't killed as a teamkill
            if data['arg4'] != -1 and data['arg3'] != data['arg6']:
                player: CreditPlayer = cast(CreditPlayer, server.get_player(id=data['arg4']))
                if player and 'use_reservations' in config and config['use_reservations']:
                    if player.deposit > 0:
                        old_points = player.points
                        player.points -= player.deposit
                        player.audit('buy', old_points, 'Points taken for being killed in a reserved module')
                        player.deposit = 0
                        # if the remaining points are not enough to stay in this plane, move them back to spectators
                        if player.points < self._get_points(server, player):
                            server.move_to_spectators(player)
        elif data['eventName'] == 'crash':
            player: CreditPlayer = cast(CreditPlayer, server.get_player(id=data['arg1']))
            if not player:
                return
            if 'use_reservations' in config and config['use_reservations']:
                if player.deposit > 0:
                    old_points = player.points
                    player.points -= player.deposit
                    player.audit('buy', old_points, 'Points taken for crashing in a reserved module')
                    player.deposit = 0
            else:
                old_points = player.points
                player.points -= self._get_costs(server, player)
                player.audit('buy', old_points, 'Points taken for crashing in a reserved module')
            if player.points < self._get_points(server, player):
                server.move_to_spectators(player)
        elif data['eventName'] == 'landing':
            # clear deposit on landing
            player: CreditPlayer = cast(CreditPlayer, server.get_player(id=data['arg1']))
            if player and player.deposit > 0:
                player.deposit = 0
        elif data['eventName'] == 'takeoff':
            # take deposit on takeoff
            if 'use_reservations' in config and config['use_reservations']:
                player: CreditPlayer = cast(CreditPlayer, server.get_player(id=data['arg1']))
                if player and player.deposit == 0 and int(player.sub_slot) == 0:
                    player.deposit = self._get_costs(server, player)
        elif data['eventName'] == 'disconnect':
            player: CreditPlayer = cast(CreditPlayer, server.get_player(id=data['arg1']))
            if player and player.deposit > 0:
                old_points = player.points
                player.points -= player.deposit
                player.audit('buy', old_points, 'Points taken for using a reserved module')
                player.deposit = 0
        elif data['eventName'] == 'mission_end':
            # give all players their credit back, if the mission ends, and they are still airborne
            for player in server.players.values():
                player.deposit = 0
