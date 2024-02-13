import discord
import os
import re

from core import Plugin, ServiceRegistry, command, utils, Node, YAMLError
from discord import app_commands
from pathlib import Path
from services import DCSServerBot, BackupService
from typing import cast

# ruamel YAML support
from ruamel.yaml import YAML
from ruamel.yaml.parser import ParserError
from ruamel.yaml.scanner import ScannerError
yaml = YAML()


async def backup_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        config = interaction.client.cogs['Backup'].locals.get('backups')
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=key.title(), value=key) for key in config.keys()
            if not current or current.casefold() in key.casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


async def date_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    async def get_all_dates(node: Node, target: str) -> list[str]:
        all_directories = await node.list_directory(target, f"{node.name.lower()}_*")

        date_pattern = re.compile(rf"{node.name.lower()}_([0-9]{{8}})")
        dates = []
        for directory in all_directories:
            match = date_pattern.match(os.path.basename(directory))
            if match:
                dates.append(match.group(1))
        return dates

    if not await interaction.command._check_can_run(interaction):
        return []
    target = interaction.client.cogs['Backup'].locals.get('target')
    node = await utils.NodeTransformer().transform(interaction, utils.get_interaction_param(interaction, "node"))
    return [
        app_commands.Choice(name=date, value=date) for date in await get_all_dates(node, target)
        if not current or current in date
    ][:25]


class Backup(Plugin):
    def __init__(self, bot: DCSServerBot):
        super().__init__(bot)
        self.service = cast(BackupService, ServiceRegistry.get("Backup"))

    def read_locals(self) -> dict:
        if not os.path.exists('config/services/backup.yaml'):
            return {}
        try:
            return yaml.load(Path('config/services/backup.yaml').read_text(encoding='utf-8'))
        except (ParserError, ScannerError) as ex:
            raise YAMLError('config/services/backup.yaml', ex)

    @command(description='Backup your data')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.autocomplete(what=backup_autocomplete)
    async def backup(self, interaction: discord.Interaction, node: app_commands.Transform[Node, utils.NodeTransformer],
                     what: str):
        ephemeral = utils.get_ephemeral(interaction)
        if what == 'database' and not node.master:
            node = self.node
        await interaction.response.defer(ephemeral=ephemeral, thinking=True)
        try:
            rc = await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Backup",
                "method": f"backup_{what}"
            }, node=node.name, timeout=300)
            assert rc['return'] is True
            await interaction.followup.send(f"Backup of {what} completed.", ephemeral=ephemeral)
        except Exception:
            await interaction.followup.send(f"Backup of {what} failed. Please check log for details",
                                            ephemeral=ephemeral)

    @command(description='Recover your data from an existing backup')
    @app_commands.guild_only()
    @utils.app_has_role('Admin')
    @app_commands.autocomplete(what=backup_autocomplete)
    @app_commands.autocomplete(date=date_autocomplete)
    async def recover(self, interaction: discord.Interaction, node: app_commands.Transform[Node, utils.NodeTransformer],
                      what: str, date: str):
        ephemeral = utils.get_ephemeral(interaction)
        if what == 'database' and not node.master:
            node = self.node
        if not await utils.yn_question(interaction, f"I am going to recover your {what} from {date}.\n"
                                                    f"This will delete **ALL** data that was there before.\n"
                                                    f"Are you 100% sure that you want to do that?",
                                       ephemeral=ephemeral):
            await interaction.followup.send("Aborted.", ephemeral=ephemeral)
            return
        try:
            rc = await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "Backup",
                "method": f"recover_{what}",
                "params": {
                    "date": date
                }
            }, node=node.name, timeout=300)
            assert rc['return'] is True
            await interaction.followup.send(f"Recovery of {what} completed.", ephemeral=ephemeral)
        except Exception:
            await interaction.followup.send(f"Recovery of {what} failed. Please check log for details",
                                            ephemeral=ephemeral)


async def setup(bot: DCSServerBot):
    await bot.add_cog(Backup(bot))
