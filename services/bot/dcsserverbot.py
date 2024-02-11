import asyncio
import discord

from contextlib import closing
from core import NodeImpl, ServiceRegistry, EventListener, Server, Channel, utils, Player, Status, FatalException
from datetime import datetime, timezone
from discord.ext import commands
from typing import Optional, Union, Tuple, TYPE_CHECKING, Any, Iterable

if TYPE_CHECKING:
    from ..servicebus import ServiceBus

__all__ = ["DCSServerBot"]


class DCSServerBot(commands.Bot):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.version: str = kwargs['version']
        self.sub_version: str = kwargs['sub_version']
        self.node: NodeImpl = kwargs['node']
        self.pool = self.node.pool
        self.log = self.node.log
        self.locals = kwargs['locals']
        self.plugins = self.node.plugins
        self.bus: ServiceBus = ServiceRegistry.get("ServiceBus")
        self.eventListeners: list[EventListener] = self.bus.eventListeners
        self.audit_channel = None
        self.mission_stats = None
        self.member: Optional[discord.Member] = None
        self.lock: asyncio.Lock = asyncio.Lock()
        self.synced: bool = False
        self.tree.on_error = self.on_app_command_error
        self._roles = None

    async def start(self, token: str, *, reconnect: bool = True) -> None:
        self.synced: bool = False
        await super().start(token, reconnect=reconnect)

    async def close(self):
        try:
            await self.audit(message="Discord Bot stopped.")
        except Exception:
            pass
        self.log.info('- Unloading Plugins ...')
        await super().close()
        self.log.info("- Stopping Services ...")

    @property
    def roles(self) -> dict[str, list[Union[str, int]]]:
        if not self._roles:
            self._roles = {
                "Admin": ["Admin"],
                "DCS Admin": ["DCS Admin"],
                "DCS": ["DCS"]
            } | self.locals.get('roles', {})
            if 'GameMaster' not in self._roles:
                self._roles['GameMaster'] = self._roles['DCS Admin']
            if 'Alert' not in self._roles:
                self._roles['Alert'] = self._roles['DCS Admin']
        return self._roles

    @property
    def filter(self) -> dict:
        return self.bus.filter

    @property
    def servers(self) -> dict[str, Server]:
        return self.bus.servers

    async def setup_hook(self) -> None:
        self.log.info('- Loading Plugins ...')
        for plugin in self.plugins:
            if not await self.load_plugin(plugin.lower()):
                self.log.info(f'  => {plugin.title()} NOT loaded.')
        # cleanup remote servers (if any)
        for key, value in self.bus.servers.copy().items():
            if value.is_remote:
                del self.bus.servers[key]

    async def load_plugin(self, plugin: str) -> bool:
        try:
            await self.load_extension(f'plugins.{plugin}.commands')
            return True
        except ModuleNotFoundError:
            self.log.error(f'  - Plugin "{plugin}" not found!')
        except commands.ExtensionNotFound:
            self.log.error(f'  - No commands.py found for plugin "{plugin}"!')
        except commands.ExtensionAlreadyLoaded:
            self.log.warning(f'  - Plugin "{plugin} was already loaded"')
        except commands.ExtensionFailed as ex:
            self.log.exception(ex.original if ex.original else ex)
        except Exception as ex:
            self.log.exception(ex)
        return False

    async def unload_plugin(self, plugin: str) -> bool:
        try:
            await self.unload_extension(f'plugins.{plugin}.commands')
            return True
        except commands.ExtensionNotFound:
            self.log.debug(f'- No init.py found for plugin "{plugin}!"')
            pass
        except commands.ExtensionNotLoaded:
            pass
        return False

    async def reload_plugin(self, plugin: str) -> bool:
        if await self.unload_plugin(plugin):
            return await self.load_plugin(plugin)
        else:
            return False

    def check_roles(self, roles: Iterable[Union[str, int]]):
        for role in roles:
            if not self.get_role(role):
                self.log.error(f"  => Role {role} not found in your Discord!")

    async def check_channel(self, channel_id: int) -> bool:
        channel = self.get_channel(channel_id)
        if not channel:
            self.log.error(f'No channel with ID {channel_id} found!')
            return False
        channel_name = channel.name.encode(encoding='ASCII', errors='replace').decode()
        # name changes of the status channel will only happen with the correct permission
        ret = True
        permissions = channel.permissions_for(self.member)
        if not permissions.view_channel:
            self.log.error(f'  => Permission "View Channel" missing for channel {channel_name}')
            ret = False
        if not permissions.send_messages:
            self.log.error(f'  => Permission "Send Messages" missing for channel {channel_name}')
            ret = False
        if not permissions.read_messages:
            self.log.error(f'  => Permission "Read Messages" missing for channel {channel_name}')
            ret = False
        if not permissions.read_message_history:
            self.log.error(f'  => Permission "Read Message History" missing for channel {channel_name}')
            ret = False
        if not permissions.add_reactions:
            self.log.error(f'  => Permission "Add Reactions" missing for channel {channel_name}')
            ret = False
        if not permissions.attach_files:
            self.log.error(f'  => Permission "Attach Files" missing for channel {channel_name}')
            ret = False
        if not permissions.embed_links:
            self.log.error(f'  => Permission "Embed Links" missing for channel {channel_name}')
            ret = False
        if not permissions.manage_messages:
            self.log.error(f'  => Permission "Manage Messages" missing for channel {channel_name}')
            ret = False
        if not permissions.use_application_commands:
            self.log.error(f'  => Permission "Use Application Commands" missing for channel {channel_name}')
            ret = False
        return ret

    def get_channel(self, id: int, /) -> Any:
        if id == -1:
            return None
        return super().get_channel(id)

    def get_role(self, role: Union[str, int]) -> Optional[discord.Role]:
        if isinstance(role, int):
            return discord.utils.get(self.guilds[0].roles, id=role)
        elif isinstance(role, str):
            if role.isnumeric():
                return self.get_role(int(role))
            else:
                return discord.utils.get(self.guilds[0].roles, name=role)
        else:
            return None

    async def check_channels(self, server: Server):
        channels = ['status', 'chat']
        if not self.locals.get('admin_channel'):
            channels.append('admin')
        if server.locals.get('coalitions'):
            channels.extend(['red', 'blue'])
        for c in channels:
            channel_id = int(server.channels[Channel(c)])
            if channel_id != -1:
                await self.check_channel(channel_id)

    async def on_ready(self):
        try:
            await self.wait_until_ready()
            if not self.synced:
                self.log.info(f'- Logged in as {self.user.name} - {self.user.id}')
                if len(self.guilds) > 1:
                    self.log.warning('  => Your bot can only be installed in ONE Discord server!')
                    for guild in self.guilds:
                        self.log.warning(f'     - {guild.name}')
                    self.log.warning('  => Remove it from one guild and restart the bot.')
                    raise FatalException()
                elif not self.guilds:
                    raise FatalException("You need to invite your bot to a Discord server.")
                self.member = await self.guilds[0].fetch_member(self.user.id)
                if not self.member:
                    raise FatalException("Can't access the bots user. Check your Discord server settings.")
                self.log.info('- Checking Roles & Channels ...')
                roles = set()
                for role in ['Admin', 'DCS Admin', 'DCS', 'GameMaster']:
                    roles |= set(self.roles[role])
                self.check_roles(roles)
                if self.locals.get('admin_channel'):
                    await self.check_channel(int(self.locals['admin_channel']))
                for server in self.servers.values():
                    if server.locals.get('coalitions'):
                        roles.clear()
                        roles |= set([x.strip() for x in server.locals['coalitions']['blue_role'].split(',')])
                        roles |= set([x.strip() for x in server.locals['coalitions']['red_role'].split(',')])
                        self.check_roles(roles)
                    try:
                        await self.check_channels(server)
                    except KeyError as ex:
                        self.log.error(f"Mandatory channel(s) missing for server {server.name} in config/servers.yaml!")

                self.log.info('- Registering Discord Commands (this might take a bit) ...')
                self.tree.copy_global_to(guild=self.guilds[0])
                await self.tree.sync(guild=self.guilds[0])
                self.synced = True
                self.log.info('- Discord Commands registered.')
                if 'discord_status' in self.locals:
                    await self.change_presence(activity=discord.Game(name=self.locals['discord_status']))
                self.log.info('DCSServerBot MASTER started, accepting commands.')
                await self.audit(message="Discord Bot started.")
            else:
                self.log.warning('- Discord connection re-established.')
        except FatalException:
            raise
        except Exception as ex:
            self.log.exception(ex)
            raise

    async def on_command_error(self, ctx: commands.Context, err: Exception):
        if isinstance(err, commands.CommandNotFound):
            pass
        elif isinstance(err, commands.NoPrivateMessage):
            await ctx.send(f"{ctx.command.name} can't be used in a DM.")
        elif isinstance(err, commands.MissingRequiredArgument):
            await ctx.send(f"Usage: {ctx.prefix}{ctx.command.name} {ctx.command.signature}")
        elif isinstance(err, commands.errors.CheckFailure):
            await ctx.send(f"You don't have the permission to use {ctx.command.name}!")
        elif isinstance(err, commands.DisabledCommand):
            pass
        elif isinstance(err, TimeoutError) or isinstance(err, asyncio.TimeoutError):
            await ctx.send('A timeout occurred. Is the DCS server running?')
        else:
            self.log.exception(err)
            await ctx.send("An unknown exception occurred.")

    async def on_app_command_error(self, interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
        if isinstance(error, discord.app_commands.CommandNotFound) or isinstance(error, discord.app_commands.CommandInvokeError):
            pass
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        if isinstance(error, discord.app_commands.NoPrivateMessage):
            await interaction.followup.send(f"{interaction.command.name} can't be used in a DM.")
        elif isinstance(error, discord.app_commands.CheckFailure):
            await interaction.followup.send(f"You don't have the permission to use {interaction.command.name}!",
                                            ephemeral=True)
        elif isinstance(error, TimeoutError) or isinstance(error, asyncio.TimeoutError):
            await interaction.followup.send('A timeout occurred. Is the DCS server running?', ephemeral=True)
        elif isinstance(error, discord.app_commands.TransformerError):
            await interaction.followup.send(error, ephemeral=True)
        else:
            self.log.exception(error)
            await interaction.followup.send("An unknown exception occurred.", ephemeral=True)

    async def reload(self, plugin: Optional[str] = None) -> bool:
        if plugin:
            return await self.reload_plugin(plugin)
        else:
            rc = True
            for plugin in self.plugins:
                if not await self.reload_plugin(plugin):
                    rc = False
            return rc

    async def audit(self, message, *, user: Optional[Union[discord.Member, str]] = None,
                    server: Optional[Server] = None):
        if not self.audit_channel:
            if 'audit_channel' in self.locals:
                self.audit_channel = self.get_channel(int(self.locals['audit_channel']))
        if self.audit_channel:
            if isinstance(user, str):
                member = self.get_member_by_ucid(user) if utils.is_ucid(user) else None
            else:
                member = user
            embed = discord.Embed(color=discord.Color.blue())
            if member:
                embed.set_author(name=member.name, icon_url=member.avatar)
                embed.set_thumbnail(url=member.avatar)
                embed.description = f'<@{member.id}> ' + message
            elif not user:
                embed.set_author(name=self.member.name, icon_url=self.member.avatar)
                embed.set_thumbnail(url=self.member.avatar)
                embed.description = message
            if isinstance(user, str):
                embed.add_field(name='UCID', value=user)
            if server:
                embed.add_field(name='Server', value=server.display_name)
            embed.set_footer(text=datetime.now(timezone.utc).strftime("%y-%m-%d %H:%M:%S"))
            await self.audit_channel.send(embed=embed, allowed_mentions=discord.AllowedMentions(replied_user=False))
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    INSERT INTO audit (node, event, server_name, discord_id, ucid)
                    VALUES (%s, %s, %s, %s, %s)
                """, (self.node.name, message, server.name if server else None,
                      user.id if isinstance(user, discord.Member) else None,
                      user if isinstance(user, str) else None))

    def get_admin_channel(self, server: Server) -> discord.TextChannel:
        admin_channel = self.locals.get('admin_channel')
        if not admin_channel:
            admin_channel = int(server.channels.get(Channel.ADMIN, -1))
        return self.get_channel(admin_channel)

    def get_ucid_by_name(self, name: str) -> Tuple[Optional[str], Optional[str]]:
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                search = f'%{name}%'
                cursor.execute('SELECT ucid, name FROM players WHERE LOWER(name) like LOWER(%s) '
                               'ORDER BY last_seen DESC LIMIT 1', (search, ))
                if cursor.rowcount >= 1:
                    res = cursor.fetchone()
                    return res[0], res[1]
                else:
                    return None, None

    def get_member_or_name_by_ucid(self, ucid: str, verified: bool = False) -> Optional[Union[discord.Member, str]]:
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                sql = 'SELECT discord_id, name FROM players WHERE ucid = %s'
                if verified:
                    sql += ' AND discord_id <> -1 AND manual IS TRUE'
                cursor.execute(sql, (ucid, ))
                if cursor.rowcount == 1:
                    row = cursor.fetchone()
                    return self.guilds[0].get_member(row[0]) or row[1]
                else:
                    return None

    def get_ucid_by_member(self, member: discord.Member, verified: Optional[bool] = False) -> Optional[str]:
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                sql = 'SELECT ucid FROM players WHERE discord_id = %s AND LENGTH(ucid) = 32 '
                if verified:
                    sql += 'AND manual IS TRUE '
                sql += 'ORDER BY last_seen DESC'
                cursor.execute(sql, (member.id, ))
                if cursor.rowcount >= 1:
                    return cursor.fetchone()[0]
                else:
                    return None

    def get_member_by_ucid(self, ucid: str, verified: Optional[bool] = False) -> Optional[discord.Member]:
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                sql = 'SELECT discord_id FROM players WHERE ucid = %s AND discord_id <> -1'
                if verified:
                    sql += ' AND manual IS TRUE'
                cursor.execute(sql, (ucid, ))
                if cursor.rowcount == 1:
                    return self.guilds[0].get_member(cursor.fetchone()[0])
                else:
                    return None

    def get_player_by_ucid(self, ucid: str, active: Optional[bool] = True) -> Optional[Player]:
        for server in self.servers.values():
            player = server.get_player(ucid=ucid, active=active)
            if player:
                return player
        return None

    def match_user(self, data: dict, rematch=False) -> Optional[discord.Member]:
        if not rematch:
            member = self.get_member_by_ucid(data['ucid'])
            if member:
                return member
        return utils.match(data['name'], [x for x in self.get_all_members() if not x.bot])

    def get_server(self, ctx: Union[discord.Interaction, discord.Message, str], *,
                   admin_only: Optional[bool] = False) -> Optional[Server]:
        if len(self.servers) == 1:
            if admin_only and int(self.locals.get('admin_channel', 0)) == ctx.channel.id:
                return list(self.servers.values())[0]
            elif not admin_only:
                return list(self.servers.values())[0]
        for server_name, server in self.servers.items():
            if isinstance(ctx, commands.Context) or isinstance(ctx, discord.Interaction) \
                    or isinstance(ctx, discord.Message):
                if server.status == Status.UNREGISTERED:
                    continue
                for channel in [Channel.ADMIN, Channel.STATUS, Channel.EVENTS, Channel.CHAT,
                                Channel.COALITION_BLUE_EVENTS, Channel.COALITION_BLUE_CHAT,
                                Channel.COALITION_RED_EVENTS, Channel.COALITION_RED_CHAT]:
                    if int(server.locals['channels'].get(channel.value, -1)) != -1 and \
                            server.channels[channel] == ctx.channel.id:
                        return server
            else:
                if server_name == ctx:
                    return server
        return None

    async def setEmbed(self, *, embed_name: str, embed: discord.Embed, channel_id: Union[Channel, int] = Channel.STATUS,
                       file: Optional[discord.File] = None, server: Optional[Server] = None):
        async with self.lock:
            if server and isinstance(channel_id, Channel):
                channel_id = int(server.channels.get(channel_id, -1))
            else:
                channel_id = int(channel_id)
            channel = self.get_channel(channel_id)
            if not channel and channel_id != -1:
                channel = await self.fetch_channel(channel_id)
            if not channel:
                self.log.error(f"Channel {channel_id} not found, can't add or change an embed in there!")
                return

            with self.pool.connection() as conn:
                # check if we have a message persisted already
                row = conn.execute("""
                    SELECT embed FROM message_persistence 
                    WHERE server_name = %s AND embed_name = %s
                """, (server.name if server else 'Master', embed_name)).fetchone()

            message = None
            if row:
                try:
                    message = await channel.fetch_message(row[0])
                    if not file:
                        await message.edit(embed=embed)
                    else:
                        await message.edit(embed=embed, attachments=[file])
                except discord.errors.NotFound:
                    message = None
                except discord.errors.DiscordException as ex:
                    self.log.warning(f"Error during update of embed {embed_name}: " + str(ex))
                    return
            if not row or not message:
                message = await channel.send(embed=embed, file=file)
                with self.pool.connection() as conn:
                    with conn.transaction():
                        conn.execute("""
                            INSERT INTO message_persistence (server_name, embed_name, embed) 
                            VALUES (%s, %s, %s) 
                            ON CONFLICT (server_name, embed_name) 
                            DO UPDATE SET embed=excluded.embed
                        """, (server.name if server else 'Master', embed_name, message.id))
