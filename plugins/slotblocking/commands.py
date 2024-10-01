import discord
import os
import psycopg

from core import Plugin, PluginRequiredError, Server, Player, TEventListener, PluginInstallationError, DEFAULT_TAG
from discord.ext import commands
from pathlib import Path
from services.bot import DCSServerBot
from typing import Optional, Type

from .listener import SlotBlockingListener

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


class SlotBlocking(Plugin):

    def __init__(self, bot: DCSServerBot, eventlistener: Type[TEventListener] = None):
        super().__init__(bot, eventlistener=eventlistener)
        if not self.locals:
            raise PluginInstallationError(reason=f"No {self.plugin_name}.yaml file found!", plugin=self.plugin_name)

    def _migrate_3_1(self, instance: dict, **kwargs):
        if instance.get('use_reservations'):
            instance['payback'] = instance.pop('use_reservations')

    def _migrate_3_2(self, instance: dict, **kwargs):
        message = kwargs['message']
        if instance.get('VIP'):
            instance['VIP']['message_server_full'] = message

    async def migrate(self, new_version: str, conn: Optional[psycopg.AsyncConnection] = None) -> None:
        if not self.locals:
            return
        kwargs = {}
        if new_version == '3.1':
            _change_instance = self._migrate_3_1
        elif new_version == '3.2':
            server_config = os.path.join(self.node.config_dir, 'servers.yaml')
            server_data = yaml.load(Path(server_config).read_text(encoding='utf-8'))
            message = server_data.get(DEFAULT_TAG, {}).get('message_server_full',
                                                           'The server is full, please try again later!')
            kwargs['message'] = message
            _change_instance = self._migrate_3_2
        else:
            return

        path = os.path.join(self.node.config_dir, 'plugins', self.plugin_name + '.yaml')
        data = yaml.load(Path(path).read_text(encoding='utf-8'))
        if self.node.name in data.keys():
            for name, node in data.items():
                if name == DEFAULT_TAG:
                    _change_instance(node, **kwargs)
                    continue
                for instance_name, instance in node.items():
                    _change_instance(instance, **kwargs)
        else:
            for instance in data.values():
                _change_instance(instance, **kwargs)
        with open(path, mode='w', encoding='utf-8') as outfile:
            yaml.dump(data, outfile)
        if new_version == '3.2':
            if DEFAULT_TAG in server_data:
                server_data[DEFAULT_TAG].pop('message_server_full', None)
            with open(server_config, mode='w', encoding='utf-8') as outfile:
                yaml.dump(server_data, outfile)

    def get_config(self, server: Optional[Server] = None, *, plugin_name: Optional[str] = None,
                   use_cache: Optional[bool] = True) -> dict:
        if plugin_name:
            return super().get_config(server, plugin_name=plugin_name, use_cache=use_cache)
        if not server:
            return self.locals.get(DEFAULT_TAG, {})
        if server.node.name not in self._config:
            self._config[server.node.name] = {}
        if server.instance.name not in self._config[server.node.name] or not use_cache:
            default, specific = self.get_base_config(server)
            vips = default.get('VIP', {}) | specific.get('VIP', {})
            self._config[server.node.name][server.instance.name] = default | specific
            if vips:
                self._config[server.node.name][server.instance.name]['VIP'] = vips
        return self._config[server.node.name][server.instance.name]

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # did a member change their roles?
        if before.roles == after.roles:
            return
        for server in self.bot.servers.values():
            player: Player = server.get_player(discord_id=after.id)
            if not player:
                ucid = await self.bot.get_ucid_by_member(after, verified=True)
                if not ucid:
                    return
                roles = [
                    self.bot.get_role(x) for x in self.get_config(server).get('VIP', {}).get('discord', [])
                ]
                if not roles:
                    return
                for role in set(before.roles) | set(after.roles):
                    if role in roles:
                        await server.send_to_dcs({
                            'command': 'uploadUserRoles',
                            'ucid': ucid,
                            'roles': [x.id for x in after.roles]
                        })
                        break


async def setup(bot: DCSServerBot):
    for plugin in ['mission', 'creditsystem']:
        if plugin not in bot.plugins:
            raise PluginRequiredError(plugin)
    await bot.add_cog(SlotBlocking(bot, SlotBlockingListener))
