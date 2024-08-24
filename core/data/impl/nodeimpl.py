import aiofiles
import aiohttp
import asyncio
import certifi
import discord
import glob
import gzip
import json
import os
import platform
import psycopg
import re
import shutil
import ssl
import subprocess
import sys

from collections import defaultdict
from contextlib import closing
from core import utils, Status, Coalition
from core.const import SAVED_GAMES
from core.translations import get_translation
from discord.ext import tasks
from packaging import version
from pathlib import Path
from psycopg.errors import UndefinedTable, InFailedSqlTransaction, NotNullViolation, OperationalError
from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import ConnectionPool, AsyncConnectionPool
from typing import Optional, Union, Awaitable, Callable, Any
from urllib.parse import urlparse, quote
from version import __version__

from core.autoexec import Autoexec
from core.data.dataobject import DataObjectFactory
from core.data.node import Node, UploadStatus, SortOrder, FatalException
from core.data.instance import Instance
from core.data.impl.instanceimpl import InstanceImpl
from core.data.server import Server
from core.data.impl.serverimpl import ServerImpl
from core.services.registry import ServiceRegistry
from core.utils.helper import SettingsDict, YAMLError

# ruamel YAML support
from pykwalify.errors import SchemaError
from pykwalify.core import Core
from ruamel.yaml import YAML
from ruamel.yaml.error import MarkedYAMLError
yaml = YAML()


__all__ = [
    "NodeImpl"
]

REPO_URL = "https://api.github.com/repos/Special-K-s-Flightsim-Bots/DCSServerBot/releases"
LOGIN_URL = 'https://www.digitalcombatsimulator.com/gameapi/login/'
UPDATER_URL = 'https://www.digitalcombatsimulator.com/gameapi/updater/branch/{}/'
LICENSES_URL = 'https://www.digitalcombatsimulator.com/checklicenses.php'

# Internationalisation
_ = get_translation('core')

# Default Plugins
DEFAULT_PLUGINS = [
    "mission",
    "scheduler",
    "help",
    "admin",
    "userstats",
    "missionstats",
    "creditsystem",
    "gamemaster",
    "cloud"
]


class NodeImpl(Node):

    def __init__(self, name: str, config_dir: Optional[str] = 'config'):
        super().__init__(name, config_dir)
        self.node = self  # to be able to address self.node
        self._public_ip: Optional[str] = None
        self.bot_version = __version__[:__version__.rfind('.')]
        self.sub_version = int(__version__[__version__.rfind('.') + 1:])
        self.is_shutdown = asyncio.Event()
        self.rc = 0
        self.dcs_branch = None
        self.dcs_version = None
        self.all_nodes: dict[str, Optional[Node]] = {self.name: self}
        self.suspect: dict[str, Node] = {}
        self.instances: list[Instance] = []
        self.update_pending = False
        self.before_update: dict[str, Callable[[], Awaitable[Any]]] = {}
        self.after_update: dict[str, Callable[[], Awaitable[Any]]] = {}
        self.locals = self.read_locals()
        if sys.platform == 'win32':
            from os import system
            system(f"title DCSServerBot v{self.bot_version}.{self.sub_version}")
        self.log.info(f'DCSServerBot v{self.bot_version}.{self.sub_version} starting up ...')
        self.log.info(f'- Python version {platform.python_version()} detected.')
        self.install_plugins()
        self.plugins: list[str] = [x.lower() for x in self.config.get('plugins', DEFAULT_PLUGINS)]
        for plugin in [x.lower() for x in self.config.get('opt_plugins', [])]:
            if plugin not in self.plugins:
                self.plugins.append(plugin)
        # make sure, cloud is loaded last
        if 'cloud' in self.plugins:
            self.plugins.remove('cloud')
            self.plugins.append('cloud')
        self.db_version = None
        self.pool: Optional[ConnectionPool] = None
        self.apool: Optional[AsyncConnectionPool] = None
        self._master = None
        self.listen_address = self.locals.get('listen_address', '127.0.0.1')
        if self.listen_address != '127.0.0.1':
            self.log.warning(
                'Please consider changing the listen_address in your nodes.yaml to 127.0.0.1 for security reasons!')
        self.listen_port = self.locals.get('listen_port', 10042)

    async def __aenter__(self):
        return self

    async def __aexit__(self, type, value, traceback):
        await self.close_db()

    async def post_init(self):
        self.pool, self.apool = await self.init_db()
        try:
            self._master = await self.heartbeat()
            self.log.info("- Starting as {} ...".format("Single / Master" if self._master else "Agent"))
        except (UndefinedTable, NotNullViolation, InFailedSqlTransaction):
            # some master tables have changed, so do the update first
            self._master = True
        if self._master:
            await self.update_db()
        self.init_instances()

    @property
    def master(self) -> bool:
        return self._master

    @master.setter
    def master(self, value: bool):
        if self._master != value:
            self._master = value

    @property
    def public_ip(self) -> str:
        return self._public_ip

    @property
    def installation(self) -> str:
        return os.path.expandvars(self.locals['DCS']['installation'])

    async def audit(self, message, *, user: Optional[Union[discord.Member, str]] = None,
                    server: Optional[Server] = None, **kwargs):
        from services.bot import BotService
        from services.servicebus import ServiceBus

        if self.master:
            await ServiceRegistry.get(BotService).bot.audit(message, user=user, server=server, **kwargs)
        else:
            params = {
                "message": message,
                "user": f"<@{user.id}>" if isinstance(user, discord.Member) else user,
                "server": server.name if server else ""
            } | kwargs
            await ServiceRegistry.get(ServiceBus).send_to_node({
                "command": "rpc",
                "service": BotService.__name__,
                "method": "audit",
                "params": params
            })

    def register_callback(self, what: str, name: str, func: Callable[[], Awaitable[Any]]):
        if what == 'before_dcs_update':
            self.before_update[name] = func
        else:
            self.after_update[name] = func

    def unregister_callback(self, what: str, name: str):
        if what == 'before_dcs_update':
            del self.before_update[name]
        else:
            del self.after_update[name]

    async def shutdown(self, rc: int = -2):
        self.rc = rc
        self.is_shutdown.set()

    async def restart(self):
        await self.shutdown(-1)

    def read_locals(self) -> dict:
        _locals = dict()
        config_file = os.path.join(self.config_dir, 'nodes.yaml')
        if os.path.exists(config_file):
            try:
                schema_files = ['schemas/nodes_schema.yaml']
                schema_files.extend([str(x) for x in Path('./extensions').rglob('*_schema.yaml')])
                c = Core(source_file=config_file, schema_files=schema_files, file_encoding='utf-8')
                try:
                    c.validate(raise_exception=True)
                except SchemaError as ex:
                    self.log.warning(f'Error while parsing {config_file}:\n{ex}')
                data: dict = yaml.load(Path(config_file).read_text(encoding='utf-8'))
            except MarkedYAMLError as ex:
                raise YAMLError('config_file', ex)
            for node_name in data.keys():
                if node_name not in self.all_nodes:
                    self.all_nodes[node_name] = None
            node: dict = data.get(self.name)
            if not node:
                raise FatalException(f'No configuration found for node {self.name} in {config_file}!')
            dirty = False
            # check if we need to secure the database URL
            database_url = node.get('database', {}).get('url')
            if database_url:
                url = urlparse(database_url)
                if url.password and url.password != 'SECRET':
                    utils.set_password('database', url.password, self.config_dir)
                    port = url.port or 5432
                    node['database']['url'] = \
                        f"{url.scheme}://{url.username}:SECRET@{url.hostname}:{port}{url.path}?sslmode=prefer"
                    dirty = True
                    self.log.info("Database password found, removing it from config.")
            password = node['DCS'].pop('dcs_password', node['DCS'].pop('password', None))
            if password:
                node['DCS']['user'] = node['DCS'].pop('dcs_user', node['DCS'].get('user'))
                utils.set_password('DCS', password, self.config_dir)
                dirty = True
            if dirty:
                with open(config_file, 'w', encoding='utf-8') as f:
                    yaml.dump(data, f)
            return node
        raise FatalException(f"No {config_file} found. Exiting.")

    async def init_db(self) -> tuple[ConnectionPool, AsyncConnectionPool]:
        url = self.config.get("database", self.locals.get('database'))['url']
        try:
            url = url.replace('SECRET', quote(utils.get_password('database', self.config_dir)) or '')
        except ValueError:
            pass
        # quick connection check
        max_attempts = self.config.get("database", self.locals.get('database')).get('max_retries', 10)
        for attempt in range(max_attempts):
            try:
                aconn = await psycopg.AsyncConnection.connect(url)
                async with aconn:
                    self.log.info("- Connection to database established.")
                    break
            except OperationalError:
                if attempt == max_attempts:
                    raise
                self.log.warning("- Database not available, trying again in 5s ...")
                await asyncio.sleep(5)
        pool_min = self.config.get("database", self.locals.get('database')).get('pool_min', 4)
        pool_max = self.config.get("database", self.locals.get('database')).get('pool_max', 10)
        max_idle = self.config.get("database", self.locals.get('database')).get('max_idle', 10 * 60.0)
        timeout = 60.0 if self.locals.get('slow_system', False) else 30.0
        self.log.debug("- Initializing database pools ...")
        db_pool = ConnectionPool(url, min_size=2, max_size=4, check=ConnectionPool.check_connection, max_idle=max_idle,
                                 timeout=timeout, open=False)
        db_apool = AsyncConnectionPool(conninfo=url, min_size=pool_min, max_size=pool_max,
                                       check=AsyncConnectionPool.check_connection, max_idle=max_idle, timeout=timeout,
                                       open=False)
        # we need to open the pools directly in here
        db_pool.open()
        await db_apool.open()
        self.log.debug("- Database pools initialized.")
        return db_pool, db_apool

    async def close_db(self):
        if not self.pool.closed:
            try:
                self.pool.close()
            except Exception as ex:
                self.log.exception(ex)
        if not self.apool.closed:
            try:
                await self.apool.close()
            except Exception as ex:
                self.log.exception(ex)

    def init_instances(self):
        grouped = defaultdict(list)
        for server_name, instance_name in utils.findDCSInstances():
            grouped[server_name].append(instance_name)
        duplicates = {
            server_name: instances
            for server_name, instances in grouped.items()
            if server_name != 'n/a' and len(instances) > 1
        }
        for server_name, instances in duplicates.items():
            self.log.warning("Duplicate server \"{}\" defined in instance {}!".format(
                server_name, ', '.join(instances)))
        for _name, _element in self.locals.pop('instances', {}).items():
            instance = DataObjectFactory().new(InstanceImpl, node=self, name=_name, locals=_element)
            self.instances.append(instance)

    async def update_db(self):
        # Initialize the database
        async with self.apool.connection() as conn:
            async with conn.transaction():
                # check if there is an old database already
                cursor = await conn.execute("""
                    SELECT tablename FROM pg_catalog.pg_tables WHERE tablename IN ('version', 'plugins')
                """)
                tables = [x[0] async for x in cursor]
                # initial setup
                if len(tables) == 0:
                    self.log.info('Creating Database ...')
                    with open(os.path.join('sql', 'tables.sql'), mode='r') as tables_sql:
                        for query in tables_sql.readlines():
                            self.log.debug(query.rstrip())
                            await cursor.execute(query.rstrip())
                    self.log.info('Database created.')
                else:
                    # version table missing (DB version <= 1.4)
                    if 'version' not in tables:
                        await conn.execute("CREATE TABLE IF NOT EXISTS version (version TEXT PRIMARY KEY)")
                        await conn.execute("INSERT INTO version (version) VALUES ('v1.4')")
                    cursor = await conn.execute('SELECT version FROM version')
                    self.db_version = (await cursor.fetchone())[0]
                    while os.path.exists(f'sql/update_{self.db_version}.sql'):
                        old_version = self.db_version
                        with open(os.path.join('sql', f'update_{self.db_version}.sql'), mode='r') as tables_sql:
                            for query in tables_sql.readlines():
                                self.log.debug(query.rstrip())
                                await conn.execute(query.rstrip())
                        cursor = await conn.execute('SELECT version FROM version')
                        self.db_version = (await cursor.fetchone())[0]
                        self.log.info(f'Database upgraded from {old_version} to {self.db_version}.')

    def install_plugins(self):
        for file in Path('plugins').glob('*.zip'):
            path = file.__str__()
            self.log.info('- Unpacking plugin "{}" ...'.format(os.path.basename(path).replace('.zip', '')))
            shutil.unpack_archive(path, '{}'.format(path.replace('.zip', '')))
            os.remove(path)

    async def _upgrade_pending_git(self) -> bool:
        import git

        try:
            with closing(git.Repo('.')) as repo:
                current_hash = repo.head.commit.hexsha
                origin = repo.remotes.origin
                origin.fetch()
                new_hash = origin.refs[repo.active_branch.name].object.hexsha
                if new_hash != current_hash:
                    return True
        except git.InvalidGitRepositoryError:
            return await self._upgrade_pending_non_git()
        except git.GitCommandError as ex:
            self.log.error('  => Autoupdate failed!')
            changed_files = repo.index.diff(None)
            if changed_files:
                self.log.error('     Please revert back the changes in these files:')
                for item in changed_files:
                    self.log.error(f'     ./{item.a_path}')
            else:
                self.log.error(ex)
            return False
        except ValueError as ex:
            self.log.error(ex)
            return False

    async def _upgrade_pending_non_git(self) -> bool:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(REPO_URL) as response:
                    result = await response.json()
                    current_version = re.sub('^v', '', __version__)
                    latest_version = re.sub('^v', '', result[0]["tag_name"])

                    if version.parse(latest_version) > version.parse(current_version):
                        return True
        except aiohttp.ClientResponseError as ex:
            # ignore rate limits
            if ex.status == 403:
                pass
            raise
        return False

    async def upgrade_pending(self) -> bool:
        self.log.debug('- Checking for updates...')
        try:
            try:
                rc = await self._upgrade_pending_git()
            except ImportError:
                rc = await self._upgrade_pending_non_git()
        except Exception as ex:
            self.log.exception(ex)
            raise
        if not rc:
            self.log.debug('- No update found for DCSServerBot.')
        return rc

    async def upgrade(self):
        # We do not want to run an upgrade, if we are on a cloud drive, so just restart in this case
        if not self.master and self.locals.get('cloud_drive', True):
            await self.restart()
            return
        elif await self.upgrade_pending():
            if self.master:
                async with self.apool.connection() as conn:
                    async with conn.transaction():
                        await conn.execute("UPDATE cluster SET update_pending = TRUE WHERE guild_id = %s",
                                           (self.guild_id, ))
            await self.shutdown(-3)

    async def get_dcs_branch_and_version(self) -> tuple[str, str]:
        if not self.dcs_branch or not self.dcs_version:
            with open(os.path.join(self.installation, 'autoupdate.cfg'), mode='r', encoding='utf8') as cfg:
                data = json.load(cfg)
            self.dcs_branch = data.get('branch', 'release')
            self.dcs_version = data['version']
            if "openbeta" in self.dcs_branch:
                self.log.debug("You're running DCS OpenBeta, which is discontinued. "
                               "Use /dcs update, if you want to switch to the release branch.")
        return self.dcs_branch, self.dcs_version

    async def update(self, warn_times: list[int], branch: Optional[str] = None) -> int:
        from services.servicebus import ServiceBus

        async def shutdown_with_warning(server: Server):
            if server.is_populated():
                shutdown_in = max(warn_times) if len(warn_times) else 0
                while shutdown_in > 0:
                    for warn_time in warn_times:
                        if warn_time == shutdown_in:
                            await server.sendPopupMessage(
                                Coalition.ALL,
                                _('Server is going down for a DCS update in {}!').format(utils.format_time(warn_time)))
                    await asyncio.sleep(1)
                    shutdown_in -= 1
            await server.shutdown(force=True)

        async def do_update(branch: Optional[str] = None) -> int:
            # disable any popup on the remote machine
            if sys.platform == 'win32':
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= (subprocess.STARTF_USESTDHANDLES | subprocess.STARTF_USESHOWWINDOW)
                startupinfo.wShowWindow = subprocess.SW_HIDE
                startupinfo.wShowWindow = subprocess.SW_HIDE
            else:
                startupinfo = None

            def run_subprocess() -> int:
                try:
                    cmd = [os.path.join(self.installation, 'bin', 'dcs_updater.exe'), '--quiet', 'update']
                    if branch:
                        cmd.append(f"@{branch}")

                    process = subprocess.run(
                        cmd, startupinfo=startupinfo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                    )
                    return process.returncode
                except Exception as ex:
                    self.log.exception(ex)
                    return -1

            rc = await asyncio.to_thread(run_subprocess)
            if branch and rc == 0:
                # check if the branch has been changed
                config = os.path.join(self.installation, 'autoupdate.cfg')
                with open(config, mode='r') as infile:
                    data = json.load(infile)
                if data['branch'] != branch:
                    data['branch'] = branch
                    with open(config, mode='w') as outfile:
                        json.dump(data, outfile, indent=2)
            return rc

        self.update_pending = True
        to_start = []
        in_maintenance = []
        tasks = []
        bus = ServiceRegistry.get(ServiceBus)
        for server in [x for x in bus.servers.values() if not x.is_remote]:
            if server.maintenance:
                in_maintenance.append(server)
            else:
                server.maintenance = True
            if server.status not in [Status.UNREGISTERED, Status.SHUTDOWN]:
                to_start.append(server)
                tasks.append(asyncio.create_task(shutdown_with_warning(server)))
        # wait for DCS servers to shut down
        if tasks:
            await asyncio.gather(*tasks)
        self.log.info(f"Updating {self.installation} ...")
        # call before update hooks
        for callback in self.before_update.values():
            await callback()
        rc = await do_update(branch)
        if rc == 0:
            self.dcs_branch = self.dcs_version = None
            if self.locals['DCS'].get('desanitize', True):
                if not self.locals['DCS'].get('cloud', False) or self.master:
                    utils.desanitize(self)
            # call after update hooks
            for callback in self.after_update.values():
                await callback()
            self.log.info(f"{self.installation} updated to the latest version.")
        for server in [x for x in bus.servers.values() if not x.is_remote]:
            if server not in in_maintenance:
                # let the scheduler do its job
                server.maintenance = False
            if server in to_start:
                try:
                    # the server was running before (being in maintenance mode), so start it again
                    await server.startup()
                except (TimeoutError, asyncio.TimeoutError):
                    self.log.warning(f'Timeout while starting {server.display_name}, please check it manually!')
        if rc == 0:
            self.update_pending = False
        return rc

    async def handle_module(self, what: str, module: str):
        if sys.platform == 'win32':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= (subprocess.STARTF_USESTDHANDLES | subprocess.STARTF_USESHOWWINDOW)
            startupinfo.wShowWindow = subprocess.SW_HIDE
        else:
            startupinfo = None

        def run_subprocess():
            subprocess.run(
                [os.path.join(self.installation, 'bin', 'dcs_updater.exe'), '--quiet', what, module],
                startupinfo=startupinfo
            )

        await asyncio.to_thread(run_subprocess)

    async def get_installed_modules(self) -> list[str]:
        with open(os.path.join(self.installation, 'autoupdate.cfg'), mode='r', encoding='utf8') as cfg:
            data = json.load(cfg)
        return data['modules']

    async def get_available_modules(self) -> list[str]:
        licenses = {
            "CAUCASUS_terrain",
            "NEVADA_terrain",
            "NORMANDY_terrain",
            "PERSIANGULF_terrain",
            "THECHANNEL_terrain",
            "SYRIA_terrain",
            "MARIANAISLANDS_terrain",
            "FALKLANDS_terrain",
            "SINAIMAP_terrain",
            "KOLA_terrain",
            "AFGHANISTAN_terrain",
            "WWII-ARMOUR",
            "SUPERCARRIER"
        }
        user = self.locals['DCS'].get('user')
        if not user:
            return list(licenses)
        password = utils.get_password('DCS', self.config_dir)
        headers = {
            'User-Agent': 'DCS_Updater/'
        }
        async with aiohttp.ClientSession(headers=headers, connector=aiohttp.TCPConnector(
                ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
            response = await session.post(LOGIN_URL, data={"login": user, "password": password})
            if response.status == 200:
                async with session.get(LICENSES_URL) as response:
                    if response.status == 200:
                        all_licenses = (await response.text(encoding='utf8')).split('<br>')[1:]
                        for lic in all_licenses:
                            if lic.endswith('_terrain'):
                                licenses.add(lic)
            return list(licenses)

    async def get_latest_version(self, branch: str) -> Optional[str]:
        async def _get_latest_version_no_auth():
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
                async with session.get(UPDATER_URL.format(branch)) as response:
                    if response.status == 200:
                        return json.loads(gzip.decompress(await response.read()))['versions2'][-1]['version']

        async def _get_latest_version_auth():
            user = self.locals['DCS'].get('user')
            password = utils.get_password('DCS', self.config_dir)
            headers = {
                'User-Agent': 'DCS_Updater/'
            }
            async with aiohttp.ClientSession(headers=headers, connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
                response = await session.post(LOGIN_URL, data={"login": user, "password": password})
                if response.status == 200:
                    async with session.get(UPDATER_URL.format(branch)) as response:
                        return json.loads(gzip.decompress(await response.read()))['versions2'][-1]['version']

        if not self.locals['DCS'].get('user'):
            return await _get_latest_version_no_auth()
        else:
            return await _get_latest_version_auth()

    async def register(self):
        self._public_ip = self.locals.get('public_ip')
        if not self._public_ip:
            self._public_ip = await utils.get_public_ip()
            self.log.info(f"- Public IP registered as: {self.public_ip}")
        if self.locals['DCS'].get('autoupdate', False):
            if not self.locals['DCS'].get('cloud', False) or self.master:
                self.autoupdate.start()
        else:
            branch, old_version = await self.get_dcs_branch_and_version()
            try:
                new_version = await self.get_latest_version(branch)
                if new_version and old_version != new_version:
                    self.log.warning(
                        f"- Your DCS World version is outdated. Consider upgrading to version {new_version}.")
            except Exception:
                self.log.warning("Version check failed, possible auth-server outage.")

    async def unregister(self):
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM nodes WHERE guild_id = %s AND node = %s", (self.guild_id, self.name))
        if self.locals['DCS'].get('autoupdate', False):
            if not self.locals['DCS'].get('cloud', False) or self.master:
                self.autoupdate.cancel()

    async def heartbeat(self) -> bool:
        def has_timeout(row: dict, timeout: int):
            return (row['now'] - row['last_seen']).total_seconds() > timeout

        try:
            async with (self.apool.connection() as conn):
                async with conn.transaction():
                    async with conn.cursor(row_factory=dict_row) as cursor:
                        try:
                            await cursor.execute("""
                                SELECT NOW() AT TIME ZONE 'UTC' AS now, * FROM nodes 
                                WHERE guild_id = %s FOR UPDATE
                            """, (self.guild_id, ))
                            all_nodes = await cursor.fetchall()
                            await cursor.execute("""
                                SELECT c.master, c.version, c.update_pending, n.node 
                                FROM cluster c LEFT OUTER JOIN nodes n
                                ON c.guild_id = n.guild_id AND c.master = n.node
                                WHERE c.guild_id = %s
                            """, (self.guild_id, ))
                            cluster = await cursor.fetchone()
                            # No master there? we take it!
                            if not cluster:
                                await cursor.execute("""
                                    INSERT INTO cluster (guild_id, master, version) VALUES (%s, %s, %s)
                                    ON CONFLICT (guild_id) DO UPDATE 
                                    SET master = excluded.master, version = excluded.version
                                """, (self.guild_id, self.name, __version__))
                                return True
                            # I am the master
                            if cluster['master'] == self.name:
                                # set the master here already to avoid race conditions
                                self.master = True
                                if cluster['update_pending']:
                                    if not await self.upgrade_pending():
                                        # we have just finished updating, so restart all other nodes (if there are any)
                                        for row in all_nodes:
                                            if row['node'] == self.name or has_timeout(row, self.locals.get('heartbeat', 60)):
                                                continue
                                            # TODO: we might not have bus access here yet, so be our own bus (dirty)
                                            data = {
                                                "command": "rpc",
                                                "object": "Node",
                                                "method": "upgrade"
                                            }
                                            await conn.execute("""
                                                INSERT INTO intercom (guild_id, node, data) VALUES (%s, %s, %s)
                                            """, (self.guild_id, row['node'], Json(data)))
                                        # clear the update flag
                                        await cursor.execute("""
                                            UPDATE cluster SET update_pending = FALSE, version = %s WHERE guild_id = %s
                                        """, (__version__, self.guild_id))
                                    else:
                                        # something went wrong, we need to upgrade again
                                        # noinspection PyAsyncCall
                                        asyncio.create_task(self.upgrade())
                                        return True
                                elif version.parse(cluster['version']) != version.parse(__version__):
                                    if version.parse(cluster['version']) > version.parse(__version__):
                                        self.log.warning(
                                            f"Bot version downgraded from {cluster['version']} to {__version__}. "
                                            f"This could lead to unexpected behavior if there have been database "
                                            f"schema changes.")
                                    await cursor.execute("UPDATE cluster SET version = %s WHERE guild_id = %s",
                                                         (__version__, self.guild_id))
                                else:
                                    from services.servicebus import ServiceBus

                                    # check all nodes
                                    for row in all_nodes:
                                        if row['node'] == self.name:
                                            continue
                                        elif self.all_nodes.get(row['node']) and has_timeout(
                                                row, self.locals.get('heartbeat', 30)):
                                            node = self.all_nodes[row['node']]
                                            self.log.warning(f"No heartbeat detected for node {node.name}")
                                            # we did not receive a heartbeat from another node
                                            if node.name in self.suspect and has_timeout(
                                                    row, self.locals.get('heartbeat', 30) * 2):
                                                self.log.error(f"Node {node.name} not responding.")
                                                await ServiceRegistry.get(ServiceBus).unregister_remote_node(node)
                                            else:
                                                self.suspect[node.name] = node
                                        elif row['node'] in self.suspect and not has_timeout(
                                                row, self.locals.get('heartbeat', 30) * 2):
                                            node = self.suspect.pop(row['node'])
                                            if not self.all_nodes.get(node.name):
                                                self.log.info(
                                                    f"- Node {row['node']} is alive again, asking for registration ...")
                                                self.all_nodes[node.name] = node
                                                await ServiceRegistry.get(ServiceBus).register_remote_servers(node)
                                return True
                            # we are not the master, the update is pending, we will not take over
                            elif cluster['update_pending']:
                                self.log.debug("A bot update is in progress. We will not take over the master node.")
                                return False
                            elif not cluster['node']:
                                await cursor.execute("UPDATE cluster SET master = %s WHERE guild_id = %s",
                                                     (self.name, self.guild_id))
                                return True
                            # we have a version mismatch on the agent, a cloud sync might still be pending
                            if version.parse(__version__) < version.parse(cluster['version']):
                                self.log.error(f"We are running version {__version__} where the master is on version "
                                               f"{cluster['version']} already. Trying to upgrade ...")
                                # TODO: we might not have bus access here yet, so be our own bus (dirty)
                                data = {
                                    "command": "rpc",
                                    "object": "Node",
                                    "method": "upgrade"
                                }
                                await cursor.execute("""
                                    INSERT INTO intercom (guild_id, node, data) VALUES (%s, %s, %s)
                                """, (self.guild_id, self.name, Json(data)))
                                return False
                            elif version.parse(__version__) > version.parse(cluster['version']):
                                self.log.warning(
                                    f"This node is running on version {__version__} where the master still runs on "
                                    f"{cluster['version']}. You need to upgrade your master node!")
                            # we are not the master, but we are the preferred one, taking over
                            if self.locals.get('preferred_master', False):
                                await cursor.execute("UPDATE cluster SET master = %s WHERE guild_id = %s",
                                                     (self.name, self.guild_id))
                                return True
                            # else, check if the running master is probably dead...
                            for row in all_nodes:
                                if row['node'] == self.name:
                                    continue
                                if row['node'] == cluster['master']:
                                    if has_timeout(row, self.locals.get('heartbeat', 30) * 2):
                                        # the master is dead, long live the master
                                        await cursor.execute("UPDATE cluster SET master = %s WHERE guild_id = %s",
                                                             (self.name, self.guild_id))
                                        return True
                                    return False
                            # we can not find a master - take over
                            await cursor.execute("UPDATE cluster SET master = %s WHERE guild_id = %s",
                                                 (self.name, self.guild_id))
                            return True
                        except UndefinedTable:
                            return True
                        except Exception as e:
                            self.log.exception(e)
                            return self.master
                        finally:
                            await cursor.execute("""
                                INSERT INTO nodes (guild_id, node) VALUES (%s, %s) 
                                ON CONFLICT (guild_id, node) DO UPDATE SET last_seen = (NOW() AT TIME ZONE 'UTC')
                            """, (self.guild_id, self.name))
        except OperationalError as ex:
            self.log.error(ex)
            return self.master

    async def get_active_nodes(self) -> list[str]:
        async with self.apool.connection() as conn:
            cursor = await conn.execute("""
                SELECT node FROM nodes 
                WHERE guild_id = %s
                AND node <> %s
                AND last_seen > (NOW() AT TIME ZONE 'UTC' - interval '1 minute')
            """, (self.guild_id, self.name))
            return [row[0] async for row in cursor]

    async def shell_command(self, cmd: str, timeout: int = 60) -> Optional[tuple[str, str]]:
        def run_subprocess():
            proc = subprocess.Popen(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            return proc.communicate(timeout=timeout)

        self.log.debug('Running shell-command: ' + cmd)
        try:
            stdout, stderr = await asyncio.to_thread(run_subprocess)
            return (stdout.decode('cp1252', 'ignore') if stdout else None,
                    stderr.decode('cp1252', 'ignore') if stderr else None)
        except subprocess.TimeoutExpired:
            raise TimeoutError()

    async def read_file(self, path: str) -> Union[bytes, int]:
        path = os.path.expandvars(path)
        if self.node.master:
            async with aiofiles.open(path, mode='rb') as file:
                return await file.read()
        else:
            async with self.apool.connection() as conn:
                async with conn.transaction():
                    async with aiofiles.open(path, mode='rb') as file:
                        await conn.execute("INSERT INTO files (guild_id, name, data) VALUES (%s, %s, %s)",
                                           (self.guild_id, path, psycopg.Binary(await file.read())))
                    cursor = await conn.execute("SELECT currval('files_id_seq')")
                    return (await cursor.fetchone())[0]

    async def write_file(self, filename: str, url: str, overwrite: bool = False) -> UploadStatus:
        if os.path.exists(filename) and not overwrite:
            return UploadStatus.FILE_EXISTS

        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status == 200:
                    try:
                        # make sure the directory exists
                        os.makedirs(os.path.dirname(filename), exist_ok=True)
                        async with aiofiles.open(filename, mode='wb') as outfile:
                            await outfile.write(await response.read())
                    except Exception as ex:
                        self.log.error(ex)
                        return UploadStatus.WRITE_ERROR
                else:
                    return UploadStatus.READ_ERROR
        return UploadStatus.OK

    async def list_directory(self, path: str, pattern: str, order: Optional[SortOrder] = SortOrder.DATE) -> list[str]:
        directory = Path(os.path.expandvars(path))
        ret = []
        for file in sorted(directory.glob(pattern), key=os.path.getmtime if order == SortOrder.DATE else None,
                           reverse=True):
            ret.append(os.path.join(directory.__str__(), file.name))
        return ret

    async def remove_file(self, path: str):
        files = glob.glob(path)
        for file in files:
            os.remove(file)

    async def rename_file(self, old_name: str, new_name: str, *, force: Optional[bool] = False):
        shutil.move(old_name, new_name, copy_function=shutil.copy2 if force else None)

    async def rename_server(self, server: Server, new_name: str):
        from services.bot import BotService
        from services.servicebus import ServiceBus

        if not self.master:
            self.log.error(
                f"Rename request received for server {server.name} that should have gone to the master node!")
            return
        # we are doing the plugin changes, as we are the master
        await ServiceRegistry.get(BotService).rename_server(server, new_name)
        # update the ServiceBus
        ServiceRegistry.get(ServiceBus).rename_server(server, new_name)
        # change the proxy name for remote servers (local ones will be renamed by ServerImpl)
        if server.is_remote:
            server.name = new_name

    @tasks.loop(minutes=5.0)
    async def autoupdate(self):
        from services.bot import BotService
        from services.servicebus import ServiceBus

        # don't run, if an update is currently running
        if self.update_pending:
            return
        try:
            try:
                branch, old_version = await self.get_dcs_branch_and_version()
                new_version = await self.get_latest_version(branch)
            except Exception:
                self.log.warning("Update check failed, possible server outage at ED.")
                return
            if new_version and old_version != new_version:
                self.log.info('A new version of DCS World is available. Auto-updating ...')
                rc = await self.update([300, 120, 60])
                if rc == 0:
                    bus = ServiceRegistry.get(ServiceBus)
                    await bus.send_to_node({
                        "command": "rpc",
                        "service": BotService.__name__,
                        "method": "audit",
                        "params": {
                            "message": f"DCS World updated to version {new_version} on node {self.node.name}."
                        }
                    })
                    if isinstance(self.locals['DCS'].get('autoupdate'), dict):
                        config = self.locals['DCS'].get('autoupdate')
                        embed = discord.Embed(
                            colour=discord.Colour.blue(),
                            title=config.get(
                                'title', 'DCS has been updated to version {}!').format(new_version),
                            url=f"https://www.digitalcombatsimulator.com/en/news/changelog/stable/{new_version}/")
                        embed.description = config.get('description', 'The following servers have been updated:')
                        embed.set_thumbnail(url="https://forum.dcs.world/uploads/monthly_2023_10/"
                                                "icons_4.png.f3290f2c17710d5ab3d0ec5f1bf99064.png")
                        embed.add_field(name=_('Server'),
                                        value='\n'.join([
                                            f'- {x.display_name}' for x in bus.servers.values() if not x.is_remote
                                        ]), inline=False)
                        embed.set_footer(
                            text=config.get('footer', 'Please make sure you update your DCS client to join!'))
                        params = {
                            "channel": config['channel'],
                            "embed": embed.to_dict()
                        }
                        if 'mention' in config:
                            params['mention'] = config['mention']
                        await bus.send_to_node({
                            "command": "rpc",
                            "service": BotService.__name__,
                            "method": "send_message",
                            "params": params
                        })
                else:
                    await ServiceRegistry.get(ServiceBus).send_to_node({
                        "command": "rpc",
                        "service": BotService.__name__,
                        "method": "alert",
                        "params": {
                            "title": "DCS Update Issue",
                            "message": f"DCS World could not be updated on node {self.name} due to an error ({rc})!"
                        }
                    })
        except aiohttp.ClientError as ex:
            self.log.warning(ex)
        except Exception as ex:
            self.log.exception(ex)

    @autoupdate.before_loop
    async def before_autoupdate(self):
        from services.servicebus import ServiceBus

        # wait for all servers to be in a proper state
        while True:
            bus = ServiceRegistry.get(ServiceBus)
            if bus and bus.servers and all(server.status != Status.UNREGISTERED for server in bus.servers.values()):
                break
            await asyncio.sleep(1)

    async def add_instance(self, name: str, *, template: str = "") -> "Instance":
        max_bot_port = max_dcs_port = max_webgui_port = -1
        for instance in self.instances:
            if instance.bot_port > max_bot_port:
                max_bot_port = instance.bot_port
            if instance.dcs_port > max_dcs_port:
                max_dcs_port = instance.dcs_port
            if instance.webgui_port > max_webgui_port:
                max_webgui_port = instance.webgui_port
        os.makedirs(os.path.join(SAVED_GAMES, name), exist_ok=True)
        instance = DataObjectFactory().new(InstanceImpl, node=self, name=name, locals={
            "bot_port": max_bot_port + 1 if max_bot_port != -1 else 6666,
            "dcs_port": max_dcs_port + 10 if max_dcs_port != -1 else 10308,
            "webgui_port": max_webgui_port + 2 if max_webgui_port != -1 else 8088
        })
        os.makedirs(os.path.join(instance.home, 'Config'), exist_ok=True)
        # should we copy from a template
        if template:
            _template = next(x for x in self.node.instances if x.name == template)
            shutil.copy2(os.path.join(_template.home, 'Config', 'autoexec.cfg'),
                         os.path.join(instance.home, 'Config'))
            shutil.copy2(os.path.join(_template.home, 'Config', 'serverSettings.lua'),
                         os.path.join(instance.home, 'Config'))
            shutil.copy2(os.path.join(_template.home, 'Config', 'options.lua'),
                         os.path.join(instance.home, 'Config'))
            shutil.copy2(os.path.join(_template.home, 'Config', 'network.vault'),
                         os.path.join(instance.home, 'Config'))
            if _template.extensions and _template.extensions.get('SRS'):
                shutil.copy2(os.path.expandvars(_template.extensions['SRS']['config']),
                             os.path.join(instance.home, 'Config', 'SRS.cfg'))
        autoexec = Autoexec(instance=instance)
        autoexec.crash_report_mode = "silent"
        config_file = os.path.join(self.config_dir, 'nodes.yaml')
        with open(config_file, mode='r', encoding='utf-8') as infile:
            config = yaml.load(infile)
        config[self.name]['instances'][instance.name] = {
            "home": instance.home,
            "bot_port": instance.bot_port
        }
        with open(config_file, mode='w', encoding='utf-8') as outfile:
            yaml.dump(config, outfile)
        settings_path = os.path.join(instance.home, 'Config', 'serverSettings.lua')
        if os.path.exists(settings_path):
            settings = SettingsDict(self, settings_path, root='cfg')
            settings['port'] = instance.dcs_port
            settings['name'] = 'n/a'
        server = DataObjectFactory().new(ServerImpl, node=self.node, port=instance.bot_port, name='n/a')
        instance.server = server
        self.instances.append(instance)
        return instance

    async def delete_instance(self, instance: Instance, remove_files: bool) -> None:
        config_file = os.path.join(self.config_dir, 'nodes.yaml')
        with open(config_file, mode='r', encoding='utf-8') as infile:
            config = yaml.load(infile)
        del config[self.name]['instances'][instance.name]
        with open(config_file, mode='w', encoding='utf-8') as outfile:
            yaml.dump(config, outfile)
        if instance.server:
            await self.unregister_server(instance.server)
        self.instances.remove(instance)
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM instances WHERE instance = %s", (instance.name, ))
        if remove_files:
            shutil.rmtree(instance.home, ignore_errors=True)

    async def rename_instance(self, instance: Instance, new_name: str) -> None:
        config_file = os.path.join(self.config_dir, 'nodes.yaml')
        with open(config_file, mode='r', encoding='utf-8') as infile:
            config = yaml.load(infile)
        new_home = os.path.join(os.path.dirname(instance.home), new_name)
        os.rename(instance.home, new_home)
        config[self.name]['instances'][new_name] = config[self.name]['instances'].pop(instance.name)
        config[self.name]['instances'][new_name]['home'] = new_home
        async with self.apool.connection() as conn:
            async with conn.transaction():
                await conn.execute("""
                    UPDATE instances SET instance = %s 
                    WHERE node = %s AND instance = %s
                """, (new_name, instance.node.name, instance.name, ))

        def change_instance_in_config(data: dict):
            if self.node.name in data and instance.name in data[self.node.name]:
                data[self.node.name][new_name] = data[self.node.name].pop(instance.name)
            elif instance.name in data:
                data[new_name] = data.pop(instance.name)

        # rename plugin configs
        for plugin in Path(os.path.join(self.config_dir, 'plugins')).glob('*.yaml'):
            data = yaml.load(plugin.read_text(encoding='utf-8'))
            change_instance_in_config(data)
            yaml.dump(data, plugin)
        # rename service configs
        for service in Path(os.path.join(self.config_dir, 'services')).glob('*.yaml'):
            data = yaml.load(service.read_text(encoding='utf-8'))
            change_instance_in_config(data)
            yaml.dump(data, service)
        instance.name = new_name
        instance.locals['home'] = new_home
        with open(config_file, mode='w', encoding='utf-8') as outfile:
            yaml.dump(config, outfile)

    async def find_all_instances(self) -> list[tuple[str, str]]:
        return utils.findDCSInstances()

    async def migrate_server(self, server: Server, instance: Instance) -> None:
        from services.servicebus import ServiceBus

        await server.node.unregister_server(server)
        server = DataObjectFactory().new(ServerImpl, node=self.node, port=instance.bot_port, name=server.name)
        instance.server = server
        ServiceRegistry.get(ServiceBus).servers[server.name] = server
        if not self.master:
            await ServiceRegistry.get(ServiceBus).send_init(server)
        server.status = Status.SHUTDOWN

    async def unregister_server(self, server: Server) -> None:
        from services.servicebus import ServiceBus

        instance = server.instance
        instance.server = None
        ServiceRegistry.get(ServiceBus).servers.pop(server.name)
