import discord
import os
import psycopg

from copy import deepcopy
from core import Plugin, utils, Server, TEventListener, Status, command, PersistentReport, ReportEnv, Channel, Group
from datetime import datetime
from discord import app_commands
from io import BytesIO
from pathlib import Path
from pprint import pprint
from services.bot import DCSServerBot
from typing import Type, Optional

from .listener import VnaoEventListener

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()
        
class Vnao(Plugin):

    def __init__(self, bot: DCSServerBot, listener: Type[TEventListener]):
        super().__init__(bot, listener)
        # Do whatever is needed to initialize your plugin.
        # You usually don't need to implement this function.
        
        # config = self.get_config()
        # nodes = yaml.load(Path('config/nodes.yaml').read_text(encoding='utf-8'))
        # for node_name, node in nodes.items():
        #     nodepath = os.path.join(config.get('greenie_boards').get('img_output_folder'), node_name)
        #     os.makedirs(nodepath, exist_ok=True)
        #     for instance_name in node.get('instances', {}).keys():
        #         os.makedirs(os.path.join(nodepath, instance_name), exist_ok=True)
        ...

    def rename(self, conn: psycopg.Connection, old_name: str, new_name: str):
        # If a server rename takes place, you might want to update data in your created tables
        # if they contain a server_name value. You usually don't need to implement this function.
        ...
        
    # New command group "/vnao"
    vnao = Group(name="vnao", description="VNAO commands")

    @vnao.command(description="Rebuilds all Greenie boards.")
    @utils.has_role("DCS Admin")
    @app_commands.guild_only()
    async def rebuild_greenie_boards(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer]):
        trap_data: dict = {}
        squadron_data: dict = {}
        
        config = self.get_config(server)

        # Build a practice board for each aircraft type
        channel_id = config.get('persistent_practice_channel', self.bot.get_admin_channel(server))
        squadron_data["is_squadron_flight"] = False
        for key, val in config['greenie_boards']["aircraft"].items():
            trap_data["airframe"] = key

            report = PersistentReport(self.bot, self.plugin_name, "greenieboard.json", embed_name=f'{server.name}-practice-{key}',
                                channel_id=channel_id,
                                server=server)
            await report.render(server_name=server.name, config=config, trap_data=deepcopy(trap_data), squadron_data=deepcopy(squadron_data))

        # Build a squadron board for each squadron
        channel_id = config.get('persistent_practice_channel', self.bot.get_admin_channel(server))
        for key, val in config['greenie_boards']["squadron_tags"].items():
            squadron_data["is_squadron_flight"] = True
            squadron_data["squadron_name"] = val["display_name"]
            squadron_data["squadron_tag"] = key
            trap_data["airframe"] = val["aircraft"]

            report = PersistentReport(self.bot, self.plugin_name, "greenieboard.json", embed_name=f'{server.name}-squadron-{key}',
                channel_id=channel_id,
                server=server)
            await report.render(server_name=server.name, config=config, trap_data=deepcopy(trap_data), squadron_data=deepcopy(squadron_data))


    @vnao.command(description='Rebuilds all Range boards.')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def rebuild_range_boards(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer]):
        config = self.get_config(server)

        report = PersistentReport(self.bot, self.plugin_name, "rangeboard.json", embed_name=f'bombboard-{server.name}',
                            channel_id = config.get('persistent_practice_channel', self.bot.get_admin_channel(server)),
                            server=server)
        await report.render(server_name=server.name, config=config, board_type='bomb')

        report = PersistentReport(self.bot, self.plugin_name, "rangeboard.json", embed_name=f'strafeboard-{server.name}',
                    channel_id = config.get('persistent_practice_channel', self.bot.get_admin_channel(server)),
                    server=server)
        await report.render(server_name=server.name, config=config, board_type='strafe')


async def setup(bot: DCSServerBot):
    await bot.add_cog(Vnao(bot, VnaoEventListener))
