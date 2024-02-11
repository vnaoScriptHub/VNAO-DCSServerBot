import asyncio
import os
import re
import shutil
import zipfile

from aiohttp import ClientSession
from contextlib import closing, suppress
from core import ServiceRegistry, Service, Server, Status, ServiceInstallationError, utils
from filecmp import cmp
from packaging import version
from pathlib import Path
from psycopg.rows import dict_row
from typing import Optional, Tuple, TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    from services import ServiceBus

__all__ = [
    "OvGMEService"
]

import sys
if sys.platform == 'win32':
    ENCODING = 'cp1252'
else:
    ENCODING = 'utf-8'


@ServiceRegistry.register("OvGME", plugin='ovgme')
class OvGMEService(Service):

    def __init__(self, node, name: str):
        super().__init__(node, name)
        if not os.path.exists('config/services/ovgme.yaml'):
            raise ServiceInstallationError(service='OvGME', reason="config/services/ovgme.yaml missing!")
        self.bus: ServiceBus = ServiceRegistry.get("ServiceBus")
        self._config = dict[str, dict]()

    async def start(self):
        await super().start()
        self.node.register_callback('before_dcs_update', self.name, self.before_dcs_update)
        self.node.register_callback('after_dcs_update', self.name, self.after_dcs_update)
        asyncio.create_task(self.install_packages())

    async def stop(self):
        self.node.unregister_callback('before_dcs_update', self.name)
        self.node.unregister_callback('after_dcs_update', self.name)
        await super().stop()

    async def before_dcs_update(self):
        # uninstall all RootFolder-packages
        self.log.debug("  => Uninstalling any OvGME-packages from the DCS installation folder ...")
        for server_name, server in self.bus.servers.items():
            for package_name, version in await self.get_installed_packages(server, 'RootFolder'):
                await self.uninstall_package(server, 'RootFolder', package_name, version)

    async def after_dcs_update(self):
        self.log.debug("  => Re-installing any OvGME-packages into the DCS installation folder ...")
        await self.install_packages()

    async def install_packages(self):
        for server_name, server in self.bus.servers.copy().items():
            if server.is_remote:
                continue
            # wait for the servers to be registered
            while server.status == Status.UNREGISTERED:
                await asyncio.sleep(1)
            config = self.get_config(server)
            if 'packages' not in config:
                return

            for package in config.get('packages', []):
                if package.get('version', 'latest') == 'latest':
                    _version = await self.get_latest_version(package)
                else:
                    _version = package['version']
                installed = self.get_installed_package(server, package['source'], package['name'])
                if (not installed or installed != _version) and \
                        server.status != Status.SHUTDOWN:
                    self.log.warning(f"  - Server {server.name} needs to be shutdown to install packages.")
                    break
                maintenance = server.maintenance
                server.maintenance = True
                try:
                    if not installed:
                        if not await self.install_package(server, package['source'], package['name'], _version,
                                                          package.get('repo')):
                            self.log.warning(f"- Package {package['name']}_v{_version} not found!")
                    elif installed != _version:
                        if version.parse(installed) > version.parse(_version):
                            self.log.debug(f"- Installed package {package['name']}_v{installed} is newer than the "
                                           f"configured version. Skipping.")
                            continue
                        if not await self.uninstall_package(server, package['source'], package['name'], installed):
                            self.log.warning(f"- Package {package['name']}_v{installed} could not be uninstalled on "
                                             f"server {server.name}!")
                        elif not await self.install_package(server, package['source'], package['name'], _version):
                            self.log.warning(f"- Package {package['name']}_v{_version} could not be installed on "
                                             f"server {server.name}!")
                        else:
                            self.log.info(f"- Package {package['name']}_v{installed} updated to v{_version}.")
                finally:
                    if maintenance:
                        server.maintenance = maintenance
                    else:
                        server.maintenance = False

    @staticmethod
    def parse_filename(filename: str) -> Tuple[Optional[str], Optional[str]]:
        if filename.endswith('.zip'):
            filename = filename[:-4]
        exp = re.compile('(?P<package>.*)_v?(?P<version>.*)')
        match = exp.match(filename)
        if match:
            return match.group('package'), match.group('version')
        else:
            return None, None

    async def get_installed_packages(self, server: Server, folder: str) -> list[Tuple[str, str]]:
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                return [
                    (x['package_name'], x['version']) for x in cursor.execute(
                        """
                            SELECT * FROM ovgme_packages 
                            WHERE server_name = %s AND folder = %s 
                            ORDER BY package_name, version
                        """, (server.name, folder))
                ]

    async def get_repo_versions(self, repo: str) -> set[str]:
        versions: set[str] = set()
        url = f"https://api.github.com/repos/{self.extract_repo_name(repo)}/releases"
        exp = re.compile(r'(\d+\.\d+(\.\d+)?)')
        async with ClientSession() as session:
            async with session.get(url) as response:
                response.raise_for_status()
                data = await response.json()
                for release in data:
                    for asset in release['assets']:
                        match = exp.search(asset['name'])
                        if match:
                            versions.add(match.group(1))
        return versions

    async def get_available_versions(self, server: Server, folder: str, package_name: str) -> list[str]:
        local_versions: set[str] = set()
        config = self.get_config(server)
        for x in Path(os.path.expandvars(config[folder])).glob(f"{package_name}*"):
            name, version = self.parse_filename(x.name)
            local_versions.add(version)
        remote_versions: set[str] = set()
        with suppress(StopIteration):
            package = next(x for x in config.get('packages', []) if x['name'] == package_name and x['source'] == folder)
            if 'repo' in package:
                remote_versions = await self.get_repo_versions(package['repo'])
        return sorted(local_versions | remote_versions)

    @staticmethod
    def extract_repo_name(url: str) -> str:
        path = urlparse(url).path
        return path.lstrip('/')

    async def download(self, url: str, folder: str, force: Optional[bool] = False) -> None:
        config = self.get_config()
        path = os.path.expandvars(config[folder])
        filename = url.split('/')[-1]
        self.log.info(f"  => OvGME: Downloading {folder}/{filename} ...")
        async with ClientSession() as session:
            async with session.get(url) as response:
                response.raise_for_status()
                outpath = os.path.join(path, filename)
                if os.path.exists(outpath) and not force:
                    self.log.warning(f"  => OvGME: File {folder}/{filename} exists!")
                    raise FileExistsError(outpath)
                with open(outpath, 'wb') as outfile:
                    outfile.write(await response.read())
        self.log.info(f"  => OvGME: {folder}/{filename} downloaded.")

    async def download_from_repo(self, repo: str, folder: str, *, package_name: Optional[str] = None,
                                 version: Optional[str] = None, force: Optional[bool] = False):
        if not package_name:
            package_name = self.extract_repo_name(repo).split('/')[-1]
        if not version or version == 'latest':
            version = await self.get_latest_repo_version(repo)
        url = f'{repo}/releases/download/v{version}/{package_name}_v{version}.zip'
        try:
            await self.download(url, folder, force)
        except Exception as ex:
            url = f'{repo}/releases/download/v{version}/{package_name}_{version}.zip'
            await self.download(url, folder, force)

    async def get_latest_repo_version(self, repo: str) -> str:
        url = f"https://api.github.com/repos/{self.extract_repo_name(repo)}/releases/latest"

        async with ClientSession() as session:
            async with session.get(url) as response:
                response.raise_for_status()
                data = await response.json()
                return data.get('tag_name', '').strip('v')

    async def _get_latest_file_version(self, package: dict):
        config = self.get_config()
        path = os.path.expandvars(config[package['source']])
        available = [self.parse_filename(x) for x in os.listdir(path) if package['name'] in x]
        max_version = None
        for _, _version in available:
            if not max_version or version.parse(_version) > version.parse(max_version):
                max_version = _version
        return max_version

    async def get_latest_version(self, package: dict) -> str:
        if 'repo' in package:
            return await self.get_latest_repo_version(package['repo'])
        else:
            return await self._get_latest_file_version(package)

    def get_installed_package(self, server: Server, folder: str, package_name: str) -> Optional[str]:
        with self.pool.connection() as conn:
            with closing(conn.cursor()) as cursor:
                cursor.execute(
                    'SELECT version FROM ovgme_packages WHERE server_name = %s AND package_name = %s AND folder = %s',
                    (server.name, package_name, folder))
                return cursor.fetchone()[0] if cursor.rowcount == 1 else None

    def recreate_install_log(self, server: Server, package_name: str, version: str) -> bool:
        config = self.get_config(server)
        path = os.path.expandvars(config['SavedGames'])
        ovgme_path = os.path.join(path, '.' + server.instance.name, package_name + '_v' + version)
        os.makedirs(ovgme_path, exist_ok=True)
        with open(os.path.join(ovgme_path, 'install.log'), 'w', encoding=ENCODING) as log:
            package = os.path.join(path, f"{package_name}_v{version}")
            if os.path.isdir(package):
                for root, dirs, files in os.walk(package):
                    for _dir in dirs:
                        log.write("w {}\n".format(os.path.relpath(os.path.join(root, _dir), package).replace('\\', '/')))
                    for file in files:
                        log.write("w {}\n".format(os.path.relpath(os.path.join(root, file), package).replace('\\', '/')))
                return True
            elif os.path.exists(package + '.zip'):
                with zipfile.ZipFile(package + '.zip', 'r') as zfile:
                    for name in zfile.namelist():
                        log.write(f"w {name}\n")
                return True
        return False

    def is_ovgme(self, zfile: zipfile.ZipFile, package_name: str) -> bool:
        dirs = ""
        for dirs in [
            x.filename.strip('/') for x in zfile.filelist if x.is_dir() and len(x.filename.strip('/').split('/')) == 1
        ]:
            if dirs.lower() in ['mods', 'scripts', 'kneeboards', 'liveries']:
                return False
        return package_name in dirs

    def do_install(self, server: Server, folder: str, package_name: str, version: str, path: str, filename: str) -> bool:
        target = self.node.installation if folder == 'RootFolder' else server.instance.home
        ovgme_path = os.path.join(path, '.' + server.instance.name, package_name + '_v' + version)
        os.makedirs(ovgme_path, exist_ok=True)
        if os.path.isfile(filename) and filename.endswith(".zip"):
            with open(os.path.join(ovgme_path, 'install.log'), 'w', encoding=ENCODING) as log:
                with zipfile.ZipFile(filename, 'r') as zfile:
                    ovgme = self.is_ovgme(zfile, package_name)
                    if ovgme:
                        root = zfile.namelist()[0]
                    for name in zfile.namelist():
                        if ovgme:
                            _name = name.replace(root, '')
                            if not _name or name in ['README.txt', 'VERSION.txt']:
                                continue
                        else:
                            _name = name
                        orig = os.path.join(target, _name)
                        if os.path.exists(orig) and os.path.isfile(orig):
                            log.write(f"x {_name}\n")
                            dest = os.path.join(ovgme_path, _name)
                            os.makedirs(os.path.dirname(dest), exist_ok=True)
                            shutil.copy2(orig, dest)
                        else:
                            log.write(f"w {_name}\n")
                        if name.endswith('/'):
                            os.makedirs(os.path.join(target, _name), exist_ok=True)
                        else:
                            with zfile.open(name) as infile:
                                with open(os.path.join(target, _name), 'wb') as outfile:
                                    outfile.write(infile.read())
        elif os.path.isdir(filename):
            with open(os.path.join(ovgme_path, 'install.log'), 'w', encoding=ENCODING) as log:
                def backup(p, names) -> list[str]:
                    _dir = p[len(os.path.join(path, package_name + '_v' + version)):].lstrip(os.path.sep)
                    for name in names:
                        source = os.path.join(p, name)
                        if len(_dir):
                            name = os.path.join(_dir, name)
                        orig = os.path.join(target, name)
                        if os.path.exists(orig) and os.path.isfile(orig) and not cmp(source, orig):
                            log.write("x {}\n".format(name.replace('\\', '/')))
                            dest = os.path.join(ovgme_path, name)
                            os.makedirs(os.path.dirname(dest), exist_ok=True)
                            shutil.copy2(orig, dest)
                        else:
                            log.write("w {}\n".format(name.replace('\\', '/')))
                    return []

                shutil.copytree(filename, target, ignore=backup, dirs_exist_ok=True)
        else:
            self.log.info(f"- Installation of package {package_name}_v{version} failed, no package.")
            return False
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    INSERT INTO ovgme_packages (server_name, package_name, version, folder) 
                    VALUES (%s, %s, %s, %s) 
                    ON CONFLICT (server_name, package_name) 
                    DO UPDATE SET version=excluded.version
                """, (server.name, package_name, version, folder))
        self.log.info(f"- Package {package_name}_v{version} successfully installed in server {server.name}.")
        return True

    async def install_package(self, server: Server, folder: str, package_name: str, version: str,
                              repo: Optional[str] = None) -> bool:
        if server.is_remote:
            return await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "OvGME",
                "method": "install_package",
                "params": {
                    "server": server.name,
                    "folder": folder,
                    "package_name": package_name,
                    "version": version,
                    "repo": repo
                }
            }, node=server.node.name)

        self.log.info(f"Installing package {package_name}_v{version} ...")
        config = self.get_config(server)
        path = os.path.expandvars(config[folder])
        os.makedirs(os.path.join(path, '.' + server.instance.name), exist_ok=True)
        try:
            filename = str(next(Path(path).glob(f"{package_name}*{version}*")))
        except StopIteration:
            if repo:
                await self.download_from_repo(repo, folder, package_name=package_name, version=version)
                return await self.install_package(server, folder, package_name, version)
            return False
        return await asyncio.create_task(asyncio.to_thread(
            self.do_install, server, folder, package_name, version, path, filename
        ))

    def do_uninstall(self, server: Server, folder: str, package_name: str, version: str, ovgme_path: str) -> bool:
        target = self.node.installation if folder == 'RootFolder' else server.instance.home
        with open(os.path.join(ovgme_path, 'install.log'), encoding=ENCODING) as log:
            lines = log.readlines()
            # delete has to run reverse to clean the directories
            for i in range(len(lines) - 1, 0, -1):
                filename = lines[i][2:].strip()
                file = os.path.normpath(os.path.join(target, filename))
                if lines[i].startswith('w'):
                    if os.path.isfile(file):
                        os.remove(file)
                    elif os.path.isdir(file):
                        with suppress(Exception):
                            os.removedirs(file)
                elif lines[i].startswith('x'):
                    try:
                        shutil.copy2(os.path.join(ovgme_path, filename), file)
                    except FileNotFoundError:
                        if folder == 'RootFolder':
                            self.log.warning(f"- Can't recover file {filename}, because it has been removed! "
                                             f"You might need to run a slow repair.")
        utils.safe_rmtree(ovgme_path)
        with self.pool.connection() as conn:
            with conn.transaction():
                conn.execute("""
                    DELETE FROM ovgme_packages 
                    WHERE server_name = %s AND folder = %s AND package_name = %s AND version = %s
                """, (server.name, folder, package_name, version))
        self.log.info(f"- Package {package_name}_v{version} successfully removed.")
        return True

    async def uninstall_package(self, server: Server, folder: str, package_name: str, version: str) -> bool:
        if server.is_remote:
            return await self.bus.send_to_node_sync({
                "command": "rpc",
                "service": "OvGME",
                "method": "uninstall_package",
                "params": {
                    "server": server.name,
                    "folder": folder,
                    "package_name": package_name,
                    "version": version
                }
            }, node=server.node.name)

        self.log.info(f"Uninstalling package {package_name}_v{version} ...")
        config = self.get_config(server)
        path = os.path.expandvars(config[folder])
        ovgme_path = os.path.join(path, '.' + server.instance.name, package_name + '_v' + version)
        if not os.path.exists(os.path.join(ovgme_path, 'install.log')):
            self.log.warning(f"- Can't find {os.path.join(ovgme_path, 'install.log')}. Trying to recreate ...")
            # try to recreate it
            if folder == 'SavedGames':
                if not await asyncio.create_task(asyncio.to_thread(
                        self.recreate_install_log, server, package_name, version
                )):
                    self.log.error(f"- Recreation failed. Can't uninstall {package_name}.")
                    return False
                else:
                    self.log.info("- Recreation successful.")
        return await asyncio.create_task(asyncio.to_thread(
            self.do_uninstall, server, folder, package_name, version, ovgme_path
        ))
