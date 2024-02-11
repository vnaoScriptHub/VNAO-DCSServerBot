import asyncio
import discord
import os
import psycopg
import re

from core import utils, Plugin, Report, Status, Server, Coalition, Channel, Player, PluginRequiredError, MizFile, \
    Group, ReportEnv, UploadStatus
from datetime import datetime, timezone
from discord import Interaction, app_commands
from discord.app_commands import Range
from discord.ext import commands, tasks
from discord.ui import Modal, TextInput
from pathlib import Path
from services import DCSServerBot
from typing import Optional

from .listener import MissionEventListener
from .views import ServerView, PresetView

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()


async def mizfile_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        server: Server = await utils.ServerTransformer().transform(interaction,
                                                                   utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        installed_missions = [os.path.expandvars(x) for x in await server.getMissionList()]
        choices: list[app_commands.Choice[int]] = [
            app_commands.Choice(name=os.path.basename(x)[:-4], value=idx)
            for idx, x in enumerate(await server.listAvailableMissions())
            if x not in installed_missions and current.casefold() in os.path.basename(x).casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


async def orig_mission_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[int]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        server: Server = await utils.ServerTransformer().transform(interaction,
                                                                   utils.get_interaction_param(interaction, 'server'))
        if not server:
            return []
        orig_files = [os.path.basename(x)[:-9] for x in await server.node.list_directory(
            await server.get_missions_dir(), '*.orig')]
        choices: list[app_commands.Choice[int]] = [
            app_commands.Choice(name=os.path.basename(x)[:-4], value=idx)
            for idx, x in enumerate(await server.getMissionList())
            if os.path.basename(x)[:-4] in orig_files and (not current or current.casefold() in x[:-4].casefold())
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


async def presets_autocomplete(interaction: discord.Interaction, current: str) -> list[app_commands.Choice[str]]:
    if not await interaction.command._check_can_run(interaction):
        return []
    try:
        choices: list[app_commands.Choice[str]] = [
            app_commands.Choice(name=x.name[:-5], value=str(x))
            for x in Path('config').glob('presets*.yaml')
            if not current or current.casefold() in x.name[:-5].casefold()
        ]
        return choices[:25]
    except Exception as ex:
        interaction.client.log.exception(ex)


class Mission(Plugin):

    def __init__(self, bot, listener):
        super().__init__(bot, listener)
        self.update_channel_name.add_exception_type(AttributeError)
        self.update_channel_name.start()
        self.afk_check.start()
        self.check_for_unban.start()

    async def cog_unload(self):
        self.check_for_unban.cancel()
        self.afk_check.cancel()
        self.update_channel_name.cancel()
        await super().cog_unload()

    def rename(self, conn: psycopg.Connection, old_name: str, new_name: str):
        conn.execute('UPDATE missions SET server_name = %s WHERE server_name = %s', (new_name, old_name))

    async def prune(self, conn: psycopg.Connection, *, days: int = -1, ucids: list[str] = None):
        self.log.debug('Pruning Mission ...')
        if days > -1:
            # noinspection PyTypeChecker
            conn.execute(f"""
                DELETE FROM missions 
                WHERE mission_end < (DATE((now() AT TIME ZONE 'utc')) - interval '{days} days')
            """)
        self.log.debug('Mission pruned.')

    async def update_ucid(self, conn: psycopg.Connection, old_ucid: str, new_ucid: str) -> None:
        conn.execute("""
            UPDATE bans SET ucid = %s WHERE ucid = %s AND NOT EXISTS (SELECT 1 FROM bans WHERE ucid = %s)
        """, (new_ucid, old_ucid, new_ucid))

    # New command group "/mission"
    mission = Group(name="mission", description="Commands to manage a DCS mission")

    @mission.command(description='Info about the running mission')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def info(self, interaction: Interaction, server: app_commands.Transform[Server, utils.ServerTransformer]):
        ephemeral = utils.get_ephemeral(interaction)
        await interaction.response.defer(ephemeral=ephemeral)
        report = Report(self.bot, self.plugin_name, 'serverStatus.json')
        env: ReportEnv = await report.render(server=server)
        try:
            file = discord.File(fp=env.buffer, filename=env.filename) if env.filename else discord.utils.MISSING
            await interaction.followup.send(embed=env.embed, file=file, ephemeral=ephemeral)
        finally:
            if env.buffer:
                env.buffer.close()

    @mission.command(description='Manage the active mission')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def manage(self, interaction: Interaction, server: app_commands.Transform[Server, utils.ServerTransformer]):
        view = ServerView(server)
        embed = await view.render(interaction)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=utils.get_ephemeral(interaction))
        try:
            await view.wait()
        finally:
            await interaction.delete_original_response()

    @mission.command(description='Information about a specific airport')
    @utils.app_has_role('DCS')
    @app_commands.guild_only()
    @app_commands.rename(idx='airport')
    @app_commands.describe(idx='Airport for ATIS information')
    @app_commands.autocomplete(idx=utils.airbase_autocomplete)
    async def atis(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(
                       status=[Status.RUNNING, Status.PAUSED])],
                   idx: int):
        if server.status not in [Status.RUNNING, Status.PAUSED]:
            await interaction.response.send_message(f"Server {server.display_name} is not running.", ephemeral=True)
            return
        airbase = server.current_mission.airbases[idx]
        data = await server.send_to_dcs_sync({
            "command": "getWeatherInfo",
            "x": airbase['position']['x'],
            "y": airbase['position']['y'],
            "z": airbase['position']['z']
        })
        report = Report(self.bot, self.plugin_name, 'atis.json')
        env = await report.render(airbase=airbase, server_name=server.display_name, data=data)
        timeout = self.bot.locals.get('message_autodelete', 300)
        await interaction.response.send_message(embed=env.embed, delete_after=timeout if timeout > 0 else None)

    @mission.command(description='Shows briefing of the active mission')
    @utils.app_has_role('DCS')
    @app_commands.guild_only()
    async def briefing(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(
                           status=[Status.RUNNING, Status.PAUSED])]):
        def read_passwords(server: Server) -> dict:
            with self.pool.connection() as conn:
                row = conn.execute(
                    'SELECT blue_password, red_password FROM servers WHERE server_name = %s',
                    (server.name,)).fetchone()
                return {"Blue": row[0], "Red": row[1]}

        if server.status not in [Status.RUNNING, Status.PAUSED]:
            await interaction.response.send_message(f"Server {server.display_name} is not running.", ephemeral=True)
            return
        timeout = self.bot.locals.get('message_autodelete', 300)
        mission_info = await server.send_to_dcs_sync({
            "command": "getMissionDetails"
        })
        mission_info['passwords'] = read_passwords(server)
        report = Report(self.bot, self.plugin_name, 'briefing.json')
        env = await report.render(mission_info=mission_info, server_name=server.name, interaction=interaction)
        await interaction.response.send_message(embed=env.embed, delete_after=timeout if timeout > 0 else None)

    @mission.command(description='Restarts the current active mission\n')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def restart(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer(
                          status=[Status.RUNNING, Status.PAUSED, Status.STOPPED])],
                      delay: Optional[int] = 120, reason: Optional[str] = None, run_extensions: Optional[bool] = False):
        await self._restart(interaction, server, delay, reason, run_extensions, rotate=False)

    @mission.command(description='Rotates the current active mission\n')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def rotate(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(
                          status=[Status.RUNNING, Status.PAUSED, Status.STOPPED])],
                     delay: Optional[int] = 120, reason: Optional[str] = None, run_extensions: Optional[bool] = False):
        await self._restart(interaction, server, delay, reason, run_extensions, rotate=True)

    async def _restart(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(
                          status=[Status.RUNNING, Status.PAUSED, Status.STOPPED])],
                       delay: Optional[int] = 120, reason: Optional[str] = None, run_extensions: Optional[bool] = False,
                       rotate: Optional[bool] = False):
        what = "restart" if not rotate else "rotate"
        actions = {
            "restart": "restarted",
            "rotate": "rotated",
        }
        ephemeral = utils.get_ephemeral(interaction)
        if server.status not in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
            await interaction.response.send_message(
                f"Can't restart server {server.display_name} as it is {server.status.name}!", ephemeral=True)
            return
        if server.restart_pending and not await utils.yn_question(interaction,
                                                                  f'A restart is currently pending.\n'
                                                                  f'Would you still like to {what} the mission?',
                                                                  ephemeral=ephemeral):
            return
        else:
            server.on_empty = dict()
        if server.is_populated():
            result = await utils.populated_question(interaction, f"Do you really want to {what} the mission?",
                                                    ephemeral=ephemeral)
            if not result:
                return
            elif result == 'later':
                server.on_empty = {"command": what, "user": interaction.user}
                server.restart_pending = True
                await interaction.followup.send(f'{what.title()} postponed when server is empty.', ephemeral=ephemeral)
                return

        server.restart_pending = True
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)
        if server.is_populated():
            if delay > 0:
                message = f"!!! Mission will be {actions.get(what)} in {utils.format_time(delay)}!!!"
            else:
                message = f"!!! Mission will be {actions.get(what)} NOW !!!"
            # have we got a message to present to the users?
            if reason:
                message += f' Reason: {reason}'

            msg = await interaction.followup.send(
                f'{what.title()}ing mission in {utils.format_time(delay)} (warning users before)...',
                ephemeral=ephemeral)
            server.sendPopupMessage(Coalition.ALL, message, sender=interaction.user.display_name)
            await asyncio.sleep(delay)
            await msg.delete()
        try:
            msg = await interaction.followup.send(f'Mission will {what} now, please wait ...', ephemeral=ephemeral)
            if rotate:
                await server.loadNextMission(modify_mission=run_extensions)
            else:
                await server.restart(modify_mission=run_extensions)
            await self.bot.audit(f'{actions.get(what)} mission', server=server, user=interaction.user)
            await msg.delete()
            await interaction.followup.send(f"Mission {actions.get(what)}.", ephemeral=ephemeral)
        except (TimeoutError, asyncio.TimeoutError):
            await interaction.followup.send(f"Timeout while {actions.get(what).replace('ed', 'ing')} the mission.\n"
                                            f"Please check with /mission info, if the server is up.",
                                            ephemeral=ephemeral)

    @mission.command(description='(Re-)Loads a mission from the list\n')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(mission_id="mission")
    @app_commands.autocomplete(mission_id=utils.mission_autocomplete)
    async def load(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(
                       status=[Status.STOPPED, Status.RUNNING, Status.PAUSED])],
                   mission_id: int, run_extensions: Optional[bool] = False):
        ephemeral = utils.get_ephemeral(interaction)
        if server.status not in [Status.RUNNING, Status.PAUSED, Status.STOPPED]:
            await interaction.response.send_message(
                f"Can't load mission on server {server.display_name} as it is {server.status.name}!", ephemeral=True)
            return
        if server.restart_pending and not await utils.yn_question(interaction,
                                                                  'A restart is currently pending.\n'
                                                                  'Would you still like to change the mission?',
                                                                  ephemeral=ephemeral):
            return
        else:
            server.on_empty = dict()

        if server.is_populated():
            result = await utils.populated_question(interaction, f"Do you really want to change the mission?",
                                                    ephemeral=ephemeral)
            if not result:
                return
        else:
            result = "yes"

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=ephemeral)
        mission = (await server.getMissionList())[mission_id]
        if server.current_mission and mission == server.current_mission.filename:
            if result == 'later':
                server.on_empty = {"command": "restart", "user": interaction.user}
                server.restart_pending = True
                await interaction.followup.send(f'Mission {server.current_mission.display_name} will be restarted '
                                                f'when server is empty.', ephemeral=ephemeral)
            else:
                await server.restart(modify_mission=run_extensions)
                await interaction.followup.send(f'Mission {server.current_mission.display_name} restarted.',
                                                ephemeral=ephemeral)
        else:
            name = os.path.basename(mission[:-4])
            if result == 'later':
                # make sure, we load that mission, independently on what happens to the server
                await server.setStartIndex(mission_id)
                server.on_empty = {"command": "load", "id": mission_id + 1, "user": interaction.user}
                server.restart_pending = True
                await interaction.followup.send(
                    f'Mission {name} will be loaded when server is empty or on the next restart.', ephemeral=ephemeral)
            else:
                tmp = await interaction.followup.send(f'Loading mission {utils.escape_string(name)} ...',
                                                      ephemeral=ephemeral)
                try:
                    await server.loadMission(mission_id + 1, modify_mission=run_extensions)
                    await self.bot.audit(f"loaded mission {utils.escape_string(name)}", server=server,
                                         user=interaction.user)
                    await interaction.followup.send(f'Mission {name} loaded.', ephemeral=ephemeral)
                except (TimeoutError, asyncio.TimeoutError):
                    await interaction.followup.send(f'Timeout while loading mission {name}.', ephemeral=ephemeral)
                finally:
                    await tmp.delete()

    @mission.command(description='Adds a mission to the list')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(idx="path")
    @app_commands.autocomplete(idx=mizfile_autocomplete)
    async def add(self, interaction: discord.Interaction,
                  server: app_commands.Transform[Server, utils.ServerTransformer], idx: int,
                  autostart: Optional[bool] = False):
        ephemeral = utils.get_ephemeral(interaction)
        await interaction.response.defer(ephemeral=ephemeral)
        all_missions = await server.listAvailableMissions()
        if idx >= len(all_missions):
            await interaction.followup.send('No mission found.', ephemeral=True)
            return
        path = all_missions[idx]
        await server.addMission(path, autostart=autostart)
        name = os.path.basename(path)[:-4]
        await interaction.followup.send(f'Mission "{utils.escape_string(name)}" added.', ephemeral=ephemeral)
        if server.status not in [Status.RUNNING, Status.PAUSED, Status.STOPPED] or \
                not await utils.yn_question(interaction, 'Do you want to load this mission?',
                                            ephemeral=ephemeral):
            return
        tmp = await interaction.followup.send(f'Loading mission {utils.escape_string(name)} ...', ephemeral=ephemeral)
        await server.loadMission(path)
        await self.bot.audit(f"loaded mission {utils.escape_string(name)}", server=server, user=interaction.user)
        await tmp.delete()
        await interaction.followup.send(f'Mission {utils.escape_string(name)} loaded.', ephemeral=ephemeral)

    @mission.command(description='Deletes a mission from the list')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(mission_id="mission")
    @app_commands.autocomplete(mission_id=utils.mission_autocomplete)
    async def delete(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer],
                     mission_id: int):
        ephemeral = utils.get_ephemeral(interaction)
        await interaction.response.defer(ephemeral=ephemeral)
        missions = await server.getMissionList()
        if mission_id >= len(missions):
            await interaction.followup.send("No mission found.")
            return
        filename = missions[mission_id]
        if server.status in [Status.RUNNING, Status.PAUSED, Status.STOPPED] and server.current_mission and \
                filename == server.current_mission.filename:
            await interaction.followup.send("You can't delete the (only) running mission.", ephemeral=True)
            return
        name = filename[:-4]

        if await utils.yn_question(interaction, f'Delete mission "{os.path.basename(name)}" from the mission list?',
                                   ephemeral=ephemeral):
            try:
                await server.deleteMission(mission_id + 1)
                await interaction.followup.send(f'Mission "{os.path.basename(name)}" removed from list.',
                                                ephemeral=ephemeral)
                if await utils.yn_question(interaction, f'Delete "{name}" also from disk?', ephemeral=ephemeral):
                    try:
                        await server.node.remove_file(filename)
                        await interaction.followup.send(f'Mission "{name}" deleted.', ephemeral=ephemeral)
                    except FileNotFoundError:
                        await interaction.followup.send(f'Mission "{name}" was already deleted.', ephemeral=ephemeral)
            except (TimeoutError, asyncio.TimeoutError):
                await interaction.followup.send("Timeout while deleting mission.\n"
                                                "Please reconfirm that the deletion was succesful.",
                                                ephemeral=ephemeral)

    @mission.command(description='Pauses the current running mission')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def pause(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])]):
        ephemeral = utils.get_ephemeral(interaction)
        if server.status == Status.RUNNING:
            await interaction.response.defer(thinking=True, ephemeral=ephemeral)
            await server.current_mission.pause()
            await interaction.followup.send(f'Server "{server.display_name}" paused.', ephemeral=ephemeral)
        else:
            await interaction.response.send_message(f'Server "{server.display_name}" is not running.', 
                                                    ephemeral=ephemeral)

    @mission.command(description='Unpauses the running mission')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def unpause(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.PAUSED])]):
        ephemeral = utils.get_ephemeral(interaction)
        if server.status == Status.PAUSED:
            await interaction.response.defer(thinking=True, ephemeral=ephemeral)
            await server.current_mission.unpause()
            await interaction.followup.send(f'Server "{server.display_name}" unpaused.', ephemeral=ephemeral)
        elif server.status == Status.RUNNING:
            await interaction.response.send_message(f'Server "{server.display_name}" is already running.',
                                                    ephemeral=ephemeral)
        else:
            await interaction.response.send_message(f"Server {server.display_name} is {server.status.name}, "
                                                    f"can't unpause.", ephemeral=ephemeral)

    @mission.command(description='Modify mission with a preset')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.autocomplete(presets_file=presets_autocomplete)
    @app_commands.rename(presets_file='presets')
    @app_commands.describe(presets_file='Select the file where you have stored your presets')
    async def modify(self, interaction: discord.Interaction,
                     server: app_commands.Transform[Server, utils.ServerTransformer(
                         status=[Status.RUNNING, Status.PAUSED, Status.STOPPED, Status.SHUTDOWN])],
                     presets_file: Optional[str] = 'config/presets.yaml'):
        ephemeral = utils.get_ephemeral(interaction)
        try:
            with open(presets_file, encoding='utf-8') as infile:
                presets = yaml.load(infile)
        except FileNotFoundError:
            await interaction.response.send_message(
                f'No presets available, please configure them in {presets_file}.', ephemeral=True)
            return
        try:
            options = [
                discord.SelectOption(label=k)
                for k, v in presets.items()
                if not isinstance(v, dict) or not v.get('hidden', False)
            ]
        except AttributeError:
            await interaction.response.send_message(
                f"There is an error in your {presets_file}. Please check the file structure.", ephemeral=True)
            return
        if len(options) > 25:
            self.log.warning("You have more than 25 presets created, you can only choose from 25!")

        result = None
        if server.status in [Status.PAUSED, Status.RUNNING]:
            question = 'Do you want to restart the server for a preset change?'
            if server.is_populated():
                result = await utils.populated_question(interaction, question, ephemeral=ephemeral)
            else:
                result = await utils.yn_question(interaction, question, ephemeral=ephemeral)
            if not result:
                return

        view = PresetView(options[:25])
        if interaction.response.is_done():
            msg = await interaction.followup.send(view=view, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(view=view, ephemeral=ephemeral)
            msg = await interaction.original_response()
        try:
            if await view.wait() or view.result is None:
                return
        finally:
            await msg.delete()
        if result == 'later':
            server.on_empty = {"command": "preset", "preset": view.result, "user": interaction.user}
            server.restart_pending = True
            await interaction.followup.send(f'Preset will be changed when server is empty.', ephemeral=ephemeral)
        else:
            startup = False
            msg = await interaction.followup.send('Changing presets...', ephemeral=ephemeral)
            if not server.node.config.get('mission_rewrite', True) and server.status != Status.STOPPED:
                await server.stop()
                startup = True
            filename = await server.get_current_mission_file()
            new_filename = await server.modifyMission(filename, [utils.get_preset(x) for x in view.result])
            message = 'Preset changed to: {}.'.format(','.join(view.result))
            if new_filename != filename:
                self.log.info(f"  => New mission written: {new_filename}")
                await server.replaceMission(int(server.settings['listStartIndex']), new_filename)
            else:
                self.log.info(f"  => Mission {filename} overwritten.")
            if startup or server.status not in [Status.STOPPED, Status.SHUTDOWN]:
                try:
                    await server.restart(modify_mission=False)
                    message += '\nMission reloaded.'
                    await self.bot.audit("changed preset {}".format(','.join(view.result)), server=server,
                                         user=interaction.user)
                    await msg.delete()
                except (TimeoutError, asyncio.TimeoutError):
                    message = ("Timeout during restart of mission!\n"
                               "Please check, if your server is running or if the mission somehow got corrupted.")
            await interaction.followup.send(message, ephemeral=ephemeral)

    @mission.command(description='Save mission preset')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def save_preset(self, interaction: discord.Interaction,
                          server: app_commands.Transform[Server, utils.ServerTransformer(
                              status=[Status.RUNNING, Status.PAUSED, Status.STOPPED])],
                          name: str):
        ephemeral = utils.get_ephemeral(interaction)
        miz = MizFile(self.bot, server.current_mission.filename)
        if os.path.exists('config/presets.yaml'):
            with open('config/presets.yaml', encoding='utf-8') as infile:
                presets = yaml.load(infile)
        else:
            presets = dict()
        if name in presets and \
                not await utils.yn_question(interaction, f'Do you want to overwrite the existing preset '
                                                         f'"{name}"?', ephemeral=ephemeral):
            return
        presets[name] = {
            "start_time": miz.start_time,
            "date": miz.date.strftime('%Y-%m-%d'),
            "temperature": miz.temperature,
            "clouds": miz.clouds,
            "wind": miz.wind,
            "groundTurbulence": miz.groundTurbulence,
            "enable_dust": miz.enable_dust,
            "dust_density": miz.dust_density if miz.enable_dust else 0,
            "qnh": miz.qnh,
            "enable_fog": miz.enable_fog,
            "fog": miz.fog if miz.enable_fog else {"thickness": 0, "visibility": 0},
            "halo": miz.halo
        }
        with open(f'config/presets.yaml', 'w', encoding='utf-8') as outfile:
            yaml.dump(presets, outfile)
        if interaction.response.is_done():
            await interaction.followup.send(f'Preset "{name}" added.', ephemeral=ephemeral)
        else:
            await interaction.response.send_message(f'Preset "{name}" added.', ephemeral=ephemeral)

    @mission.command(description='Rollback to the original mission file after any modifications')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    @app_commands.rename(mission_id="mission")
    @app_commands.autocomplete(mission_id=orig_mission_autocomplete)
    async def rollback(self, interaction: discord.Interaction,
                       server: app_commands.Transform[Server, utils.ServerTransformer(status=[
                           Status.RUNNING, Status.PAUSED, Status.STOPPED])], mission_id: int):
        missions = await server.getMissionList()
        if mission_id >= len(missions):
            await interaction.response.send_message("No mission found.")
            return
        filename = missions[mission_id]
        if server.status in [Status.RUNNING, Status.PAUSED] and filename == server.current_mission.filename:
            await interaction.response.send_message("Please stop your server first to rollback the running mission.",
                                                    ephemeral=True)
            return
        mission_folder = await server.get_missions_dir()
        miz_file = os.path.basename(filename)
        try:
            new_file = os.path.join(mission_folder, miz_file)
            old_file = new_file + '.orig'
            await server.node.rename_file(old_file, new_file, force=True)
        except FileNotFoundError:
            # we should never be here, but just in case
            await interaction.response.send_message("No orig file there, the mission was not changed.", ephemeral=True)
            return
        if new_file != filename:
            await server.replaceMission(mission_id, new_file)
        await interaction.response.send_message(f"Mission {miz_file[:-4]} has been rolled back.",
                                                ephemeral=utils.get_ephemeral(interaction))

    # New command group "/player"
    player = Group(name="player", description="Commands to manage DCS players")

    @player.command(name='list', description='Lists the current players')
    @app_commands.guild_only()
    @utils.app_has_role('DCS')
    async def _list(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])]):
        if server.status != Status.RUNNING:
            await interaction.response.send_message(f"Server {server.display_name} is not running.", ephemeral=True)
            return
        report = Report(self.bot, self.plugin_name, 'players.json')
        env = await report.render(server=server, sides=utils.get_sides(interaction.client, interaction, server))
        await interaction.response.send_message(embed=env.embed, ephemeral=utils.get_ephemeral(interaction))

    @player.command(description='Kicks a player by name or UCID')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def kick(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                   player: app_commands.Transform[Player, utils.PlayerTransformer(active=True)],
                   reason: Optional[str] = 'n/a') -> None:
        if not player:
            await interaction.response.send_message("Player not found.", ephemeral=True)
            return
        server.kick(player, reason)
        await self.bot.audit(f'kicked player {player.display_name} with reason "{reason}"', user=interaction.user)
        await interaction.response.send_message(f"Player {player.display_name} (ucid={player.ucid}) kicked.",
                                                ephemeral=utils.get_ephemeral(interaction))

    @player.command(description='Bans a player from a running server')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def ban(self, interaction: discord.Interaction,
                  server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                  player: app_commands.Transform[Player, utils.PlayerTransformer(active=True)]):

        class BanModal(Modal):
            reason = TextInput(label="Reason", default="n/a", max_length=80, required=False)
            period = TextInput(label="Days (empty = forever)", required=False)

            def __init__(self, server: Server, player: Player):
                super().__init__(title="Ban Details")
                self.server = server
                self.player = player

            async def on_submit(derived, interaction: discord.Interaction):
                days = int(derived.period.value) if derived.period.value else None
                self.bus.ban(derived.player.ucid, interaction.user.display_name, derived.reason.value, days)
                await interaction.response.send_message(f"Player {player.display_name} banned on all servers " +
                                                        (f"for {days} days." if days else ""),
                                                        ephemeral=utils.get_ephemeral(interaction))
                await self.bot.audit(f'banned player {player.display_name} with reason "{derived.reason.value}"' +
                                     (f' for {days} days.' if days else ' permanently.'), user=interaction.user)
        if not player:
            await interaction.response.send_message("Player not found.", ephemeral=True)
            return
        await interaction.response.send_modal(BanModal(server, player))

    @player.command(description='Moves a player to spectators')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def spec(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                   player: app_commands.Transform[Player, utils.PlayerTransformer(active=True)],
                   reason: Optional[str] = 'n/a') -> None:
        if not player:
            await interaction.response.send_message("Player not found.", ephemeral=True)
            return
        server.move_to_spectators(player)
        if reason:
            player.sendChatMessage(f"You have been moved to spectators. Reason: {reason}",
                                   interaction.user.display_name)
        await self.bot.audit(f'moved player {player.name} to spectators with reason "{reason}".', user=interaction.user)
        await interaction.response.send_message(f'User "{player.name}" moved to spectators.',
                                                ephemeral=utils.get_ephemeral(interaction))

    @player.command(description='List of AFK players')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def afk(self, interaction: discord.Interaction,
                  server: Optional[app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])]],
                  minutes: Optional[int] = 10):
        if server.status != Status.RUNNING:
            await interaction.response.send_message(f"Server {server.display_name} is not running.", ephemeral=True)
            return
        ephemeral = utils.get_ephemeral(interaction)
        afk: list[Player] = list()
        for s in self.bot.servers.values():
            if server and s != server:
                continue
            for ucid, dt in s.afk.items():
                player = s.get_player(ucid=ucid, active=True)
                if not player:
                    continue
                if (datetime.now() - dt).total_seconds() > minutes * 60:
                    afk.append(player)

        if afk:
            title = 'AFK Players'
            if server:
                title += f' on {server.name}'
            embed = discord.Embed(title=title, color=discord.Color.blue())
            embed.description = f'These players are AFK for more than {minutes} minutes:'
            for player in sorted(afk, key=lambda x: x.server.name):
                embed.add_field(name='Name', value=player.display_name)
                embed.add_field(name='Time',
                                value=utils.format_time(int((datetime.now(timezone.utc) -
                                                             player.server.afk[player.ucid]).total_seconds())))
                if server:
                    embed.add_field(name='_ _', value='_ _')
                else:
                    embed.add_field(name='Server', value=player.server.display_name)
            await interaction.response.send_message(embed=embed, ephemeral=ephemeral)
        else:
            await interaction.response.send_message(f"No player is AFK for more than {minutes} minutes.",
                                                    ephemeral=ephemeral)

    @player.command(description='Sends a popup to a player\n')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def popup(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                    player: app_commands.Transform[Player, utils.PlayerTransformer(active=True)],
                    message: str, time: Optional[Range[int, 1, 30]] = -1):
        if not player:
            await interaction.response.send_message("Player not found.", ephemeral=True)
            return
        player.sendPopupMessage(message, time, interaction.user.display_name)
        await interaction.response.send_message('Message sent.', ephemeral=utils.get_ephemeral(interaction))

    @player.command(description='Sends a chat message to a player')
    @app_commands.guild_only()
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def chat(self, interaction: discord.Interaction,
                   server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                   player: app_commands.Transform[Player, utils.PlayerTransformer(active=True)], message: str):
        if not player:
            await interaction.response.send_message("Player not found.", ephemeral=True)
            return
        player.sendChatMessage(message, interaction.user.display_name)
        await interaction.response.send_message('Message sent.', ephemeral=utils.get_ephemeral(interaction))

    @player.command(description='Moves a player onto the watchlist')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def watch(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                    player: app_commands.Transform[Player, utils.PlayerTransformer(active=True, watchlist=False)]):
        if not player:
            await interaction.response.send_message("Player not found.", ephemeral=True)
            return
        player.watchlist = True
        await interaction.response.send_message(f"Player {player.display_name} is now on the watchlist.",
                                                ephemeral=utils.get_ephemeral(interaction))

    @player.command(description='Removes a player from the watchlist')
    @app_commands.guild_only()
    @utils.app_has_role('DCS Admin')
    async def unwatch(self, interaction: discord.Interaction,
                      server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                      player: app_commands.Transform[Player, utils.PlayerTransformer(active=True, watchlist=True)]):
        if not player:
            await interaction.response.send_message("Player not found.", ephemeral=True)
            return
        player.watchlist = False
        await interaction.response.send_message(f"Player {player.display_name} removed from watchlist.",
                                                ephemeral = utils.get_ephemeral(interaction))

    # New command group "/group"
    group = Group(name="group", description="Commands to manage DCS groups")

    @group.command(description='Sends a popup to a group\n')
    @app_commands.guild_only()
    @app_commands.autocomplete(group=utils.group_autocomplete)
    @utils.app_has_roles(['DCS Admin', 'GameMaster'])
    async def popup(self, interaction: discord.Interaction,
                    server: app_commands.Transform[Server, utils.ServerTransformer(status=[Status.RUNNING])],
                    group: str, message: str, time: Optional[Range[int, 1, 30]] = -1):
        server.sendPopupMessage(group, message, time, interaction.user.display_name)
        await interaction.response.send_message('Message sent.', ephemeral=utils.get_ephemeral(interaction))

    @tasks.loop(minutes=1.0)
    async def check_for_unban(self):
        with self.pool.connection() as conn:
            with conn.transaction():
                for row in conn.execute("""
                    SELECT ucid FROM bans WHERE banned_until < (NOW() AT TIME ZONE 'utc')
                """):
                    for server in self.bot.servers.values():
                        if server.status not in [Status.PAUSED, Status.RUNNING, Status.STOPPED]:
                            continue
                        server.send_to_dcs({
                            "command": "unban",
                            "ucid": row[0]
                        })
                    # delete unbanned accounts from the database
                    conn.execute("DELETE FROM bans WHERE ucid = %s", row[0])

    @check_for_unban.before_loop
    async def before_check_unban(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=5.0)
    async def update_channel_name(self):
        # might happen during a restart
        if not self.bot.member:
            return
        for server_name, server in self.bot.servers.copy().items():
            if server.status == Status.UNREGISTERED:
                continue
            try:
                # channel = await self.bot.fetch_channel(int(server.locals['channels'][Channel.STATUS.value]))
                channel = self.bot.get_channel(server.channels[Channel.STATUS])
                if not channel:
                    channel = await self.bot.fetch_channel(server.channels[Channel.STATUS])
                # name changes of the status channel will only happen with the correct permission
                if channel.permissions_for(self.bot.member).manage_channels:
                    name = channel.name
                    # if the server owner leaves, the server is shut down
                    if server.status in [Status.STOPPED, Status.SHUTDOWN, Status.LOADING]:
                        if name.find('［') == -1:
                            name = name + '［-］'
                        else:
                            name = re.sub('［.*］', f'［-］', name)
                    else:
                        players = server.get_active_players()
                        current = len(players) + 1
                        max_players = server.settings.get('maxPlayers') or 0
                        if name.find('［') == -1:
                            name = name + f'［{current}／{max_players}］'
                        else:
                            name = re.sub('［.*］', f'［{current}／{max_players}］', name)
                    if name != channel.name:
                        await channel.edit(name=name)
            except Exception as ex:
                self.log.debug(f"Exception in update_channel_name() for server {server_name}", exc_info=str(ex))

    @update_channel_name.before_loop
    async def before_update_channel_name(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=1.0)
    async def afk_check(self):
        try:
            for server in self.bot.servers.values():
                max_time = server.locals.get('afk_time', -1)
                if max_time == -1:
                    continue
                for ucid, dt in server.afk.items():
                    player = server.get_player(ucid=ucid, active=True)
                    if not player or player.has_discord_roles(['DCS Admin', 'GameMaster']):
                        continue
                    if (datetime.now(timezone.utc) - dt).total_seconds() > max_time:
                        msg = self.get_config(server).get(
                            'message_afk', '{player.name}, you have been kicked for being AFK for '
                                           'more than {time}.'.format(player=player, time=utils.format_time(max_time)))
                        server.kick(player, msg)
        except Exception as ex:
            self.log.exception(ex)

    @afk_check.before_loop
    async def before_afk_check(self):
        await self.bot.wait_until_ready()

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # ignore bot messages or messages that do not contain miz attachments
        if message.author.bot or not message.attachments or not message.attachments[0].filename.endswith('.miz'):
            return
        # only DCS Admin role is allowed to upload missions
        if not utils.check_roles(self.bot.roles['DCS Admin'], message.author):
            return
        # check if the upload happens in the servers admin channel (if provided)
        server: Server = self.bot.get_server(message, admin_only=True)
        ctx = await self.bot.get_context(message)
        if not server:
            # check if there is a central admin channel configured
            if self.bot.locals.get('admin_channel', 0) == message.channel.id:
                try:
                    server = await utils.server_selection(
                        self.bus, ctx, title="To which server do you want to upload this mission to?")
                    if not server:
                        await ctx.send('Upload aborted.')
                        return
                except Exception as ex:
                    self.log.exception(ex)
                    return
            else:
                return
        att = message.attachments[0]
        try:
            rc = await server.uploadMission(att.filename, att.url)
            if rc == UploadStatus.FILE_IN_USE:
                if not await utils.yn_question(ctx, 'A mission is currently active.\n'
                                               'Do you want me to stop the DCS-server to replace it?'):
                    await message.channel.send('Upload aborted.')
                    return
            elif rc == UploadStatus.FILE_EXISTS:
                if not await utils.yn_question(ctx, 'File exists. Do you want to overwrite it?'):
                    await message.channel.send('Upload aborted.')
                    return
            if rc != UploadStatus.OK:
                await server.uploadMission(att.filename, att.url, force=True)

            filename = os.path.normpath(os.path.join(await server.get_missions_dir(), att.filename))
            name = utils.escape_string(os.path.basename(att.filename)[:-4])
            await message.channel.send(f'Mission "{name}" uploaded to server {server.name} and added.')
            await self.bot.audit(f'uploaded mission "{name}"', server=server, user=message.author)

            if (server.status != Status.SHUTDOWN and server.current_mission and
                    server.current_mission.filename != filename and
                    await utils.yn_question(ctx, 'Do you want to load this mission?')):
                extensions = [
                    x.name for x in server.extensions.values()
                    if getattr(x, 'beforeMissionLoad').__module__ != 'core.extension'
                ]
                if len(extensions):
                    modify = await utils.yn_question(ctx, "Do you want to apply extensions before mission start?")
                else:
                    modify = False
                tmp = await message.channel.send(f'Loading mission {name} ...')
                try:
                    await server.loadMission(filename, modify_mission=modify)
                except (TimeoutError, asyncio.TimeoutError):
                    await tmp.delete()
                    await message.channel.send(f"Timeout while trying to load mission.")
                    await self.bot.audit(f"Timeout while trying to load mission {name}",
                                         server=server)
                    return
                await self.bot.audit(f"loaded mission {name}", server=server, user=message.author)
                await tmp.delete()
                await message.channel.send(f'Mission {name} loaded.')
        except Exception as ex:
            self.log.exception(ex)
        finally:
            await message.delete()

    @commands.Cog.listener()
    async def on_member_ban(self, guild: discord.Guild, member: discord.Member):
        self.bot.log.debug(f"Member {member.display_name} has been banned.")
        if not self.bot.locals.get('no_dcs_autoban', False):
            ucid = self.bot.get_ucid_by_member(member)
            if ucid:
                self.bus.ban(ucid, 'Discord',
                             self.bot.locals.get('message_ban', 'User has been banned on Discord.'))


async def setup(bot: DCSServerBot):
    if 'gamemaster' not in bot.plugins:
        raise PluginRequiredError('gamemaster')
    await bot.add_cog(Mission(bot, MissionEventListener))
