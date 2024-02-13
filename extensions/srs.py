import aiohttp
import asyncio
import certifi
import os
import shutil
import subprocess
import ssl
import sys

from discord.ext import tasks
from configparser import RawConfigParser
from core import Extension, utils, Server
from typing import Optional

ports: dict[int, str] = dict()
SRS_GITHUB_URL = "https://github.com/ciribob/DCS-SimpleRadioStandalone/releases/latest"


class SRS(Extension):
    def __init__(self, server: Server, config: dict):
        self.cfg = RawConfigParser()
        self.cfg.optionxform = str
        super().__init__(server, config)
        self.process = None

    def load_config(self) -> Optional[dict]:
        if 'config' in self.config:
            self.cfg.read(os.path.expandvars(self.config['config']), encoding='utf-8')
            return {s: dict(self.cfg.items(s)) for s in self.cfg.sections()}
        else:
            return {}

    def enable_autoconnect(self):
        # Change DCS-SRS-AutoConnectGameGUI.lua if necessary
        autoconnect = os.path.join(self.server.instance.home,
                                   os.path.join('Scripts', 'Hooks', 'DCS-SRS-AutoConnectGameGUI.lua'))
        host = self.config.get('host', self.node.public_ip)
        port = self.config.get('port', self.locals['Server Settings']['SERVER_PORT'])
        if os.path.exists(autoconnect):
            shutil.copy2(autoconnect, autoconnect + '.bak')
        with open(os.path.join('extensions', 'lua', 'DCS-SRS-AutoConnectGameGUI.lua'), mode='r',
                  encoding='utf-8') as infile:
            with open(autoconnect, mode='w', encoding='utf-8') as outfile:
                for line in infile.readlines():
                    if line.startswith('SRSAuto.SERVER_SRS_HOST_AUTO = '):
                        line = "SRSAuto.SERVER_SRS_HOST_AUTO = false -- if set to true SRS will set the " \
                               "SERVER_SRS_HOST for you! - Currently disabled\n"
                    elif line.startswith('SRSAuto.SERVER_SRS_PORT = '):
                        line = f'SRSAuto.SERVER_SRS_PORT = "{port}" --  SRS Server default is 5002 TCP & UDP\n'
                    elif line.startswith('SRSAuto.SERVER_SRS_HOST = '):
                        line = f'SRSAuto.SERVER_SRS_HOST = "{host}" -- overridden if SRS_HOST_AUTO is true ' \
                               f'-- set to your PUBLIC ipv4 address\n'
                    outfile.write(line)

    def disable_autoconnect(self):
        autoconnect = os.path.join(self.server.instance.home,
                                   os.path.join('Scripts', 'Hooks', 'DCS-SRS-AutoConnectGameGUI.lua'))
        if os.path.exists(autoconnect):
            shutil.copy2(autoconnect, autoconnect + '.bak')
            os.remove(autoconnect)

    async def prepare(self) -> bool:
        global ports

        # Set SRS port if necessary
        dirty = False
        if 'port' in self.config and int(self.cfg['Server Settings']['SERVER_PORT']) != int(self.config['port']):
            self.cfg.set('Server Settings', 'SERVER_PORT', str(self.config['port']))
            self.log.info(f"  => {self.server.name}: SERVER_PORT set to {self.config['port']}")
            dirty = True
        if 'awacs' in self.config and self.cfg['General Settings']['EXTERNAL_AWACS_MODE'] != str(self.config['awacs']).lower():
            self.cfg.set('General Settings', 'EXTERNAL_AWACS_MODE', str(self.config['awacs']).lower())
            self.log.info(f"  => {self.server.name}: EXTERNAL_AWACS_MODE set to {self.config['awacs']}")
            dirty = True
        if 'blue_password' in self.config and self.cfg['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_BLUE_PASSWORD'] != self.config['blue_password']:
            self.cfg.set('External AWACS Mode Settings', 'EXTERNAL_AWACS_MODE_BLUE_PASSWORD', self.config['blue_password'])
            self.log.info(f"  => {self.server.name}: EXTERNAL_AWACS_MODE_BLUE_PASSWORD set to {self.config['blue_password']}")
            dirty = True
        if 'red_password' in self.config and self.cfg['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_RED_PASSWORD'] != self.config['red_password']:
            self.cfg.set('External AWACS Mode Settings', 'EXTERNAL_AWACS_MODE_RED_PASSWORD', self.config['red_password'])
            self.log.info(f"  => {self.server.name}: EXTERNAL_AWACS_MODE_RED_PASSWORD set to {self.config['red_password']}")
            dirty = True
        if dirty:
            path = os.path.expandvars(self.config['config'])
            with open(path, mode='w', encoding='utf-8') as ini:
                self.cfg.write(ini)
            self.locals = self.load_config()
        # Check port conflicts
        port = self.config.get('port', int(self.cfg['Server Settings'].get('SERVER_PORT', '5002')))
        if port in ports and ports[port] != self.server.name:
            self.log.error(f"  => {self.server.name}: {self.name} port {port} already in use by server {ports[port]}!")
            return False
        else:
            ports[port] = self.server.name
        if self.config.get('autoconnect', True):
            self.enable_autoconnect()
            self.log.info('  => SRS autoconnect is enabled for this server.')
        else:
            self.log.info('  => SRS autoconnect is NOT enabled for this server.')
        return await super().prepare()

    async def startup(self) -> bool:
        await super().startup()
        if self.config.get('autostart', True):
            self.log.debug(f"Launching SRS server with: \"{self.get_exe_path()}\" -cfg=\"{self.config['config']}\"")
            if sys.platform == 'win32' and self.config.get('minimized', False):
                import win32con

                info = subprocess.STARTUPINFO()
                info.dwFlags = subprocess.STARTF_USESHOWWINDOW
                info.wShowWindow = win32con.SW_MINIMIZE
            else:
                info = None
            out = asyncio.subprocess.DEVNULL if not self.config.get('debug', False) else None
            self.process = await asyncio.create_subprocess_exec(
                self.get_exe_path(),
                '-cfg={}'.format(os.path.expandvars(self.config['config'])),
                startupinfo=info, stdout=out, stderr=out)
        return self.is_running()

    async def shutdown(self):
        if self.config.get('autostart', True) and not self.config.get('no_shutdown', False):
            p = self.process or utils.find_process('SR-Server.exe', self.server.instance.name)
            if p:
                p.kill()
                self.process = None
            return await super().shutdown()

    def is_running(self) -> bool:
        server_ip = self.locals['Server Settings'].get('SERVER_IP', '127.0.0.1')
        if server_ip == '0.0.0.0':
            server_ip = '127.0.0.1'
        return utils.is_open(server_ip, self.locals['Server Settings'].get('SERVER_PORT', 5002))

    def get_inst_path(self) -> str:
        return os.path.join(
            os.path.expandvars(self.config.get('installation',
                                               os.path.join('%ProgramFiles%', 'DCS-SimpleRadio-Standalone'))))

    def get_exe_path(self) -> str:
        return os.path.join(self.get_inst_path(), 'SR-Server.exe')

    @property
    def version(self) -> Optional[str]:
        return utils.get_windows_version(self.get_exe_path())

    async def render(self, param: Optional[dict] = None) -> dict:
        if self.locals:
            host = self.config.get('host', self.node.public_ip)
            value = f"{host}:{self.locals['Server Settings']['SERVER_PORT']}"
            show_passwords = self.config.get('show_passwords', True)
            if show_passwords and self.locals['General Settings']['EXTERNAL_AWACS_MODE'] == 'true' and \
                    'External AWACS Mode Settings' in self.locals:
                blue = self.locals['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_BLUE_PASSWORD']
                red = self.locals['External AWACS Mode Settings']['EXTERNAL_AWACS_MODE_RED_PASSWORD']
                if blue or red:
                    value += f'\n🔹 Pass: {blue}\n🔸 Pass: {red}'
            return {
                "name": self.name,
                "version": self.version,
                "value": value
            }

    def is_installed(self) -> bool:
        # check if SRS is installed
        exe_path = self.get_exe_path()
        if not os.path.exists(exe_path):
            self.log.error(f"  => SRS executable not found in {exe_path}")
            return False
        # do we have a proper config file?
        try:
            cfg_path = os.path.expandvars(self.config.get('config'))
            if not os.path.exists(cfg_path):
                self.log.error(f"  => SRS config not found for server {self.server.name}")
                return False
            if self.server.instance.name not in cfg_path:
                self.log.warning(f"  => Please move your SRS configuration from {cfg_path} to "
                                 f"{os.path.join(self.server.instance.home, 'Config', 'SRS.cfg')}")
            return True
        except KeyError:
            self.log.error(f"  => SRS config not set for server {self.server.name}")
            return False

    @tasks.loop(minutes=5)
    async def schedule(self):
        if not self.config.get('autoupdate', False):
            return
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(
                    ssl=ssl.create_default_context(cafile=certifi.where()))) as session:
                async with session.get(SRS_GITHUB_URL) as response:
                    if response.status in [200, 302]:
                        version = response.url.raw_parts[-1]
                        if version != self.version:
                            self.log.info(f"A new DCS-SRS update is available. Updating to version {version} ...")
                            cwd = self.get_inst_path()
                            subprocess.run(executable=os.path.join(cwd, 'SRS-AutoUpdater.exe'),
                                           args=['-server', '-autoupdate', f'-path=\"{cwd}\"'], cwd=cwd, shell=True)
        except OSError as ex:
            if ex.winerror == 740:
                self.log.error("You need to run DCSServerBot as Administrator to use the DCS-SRS AutoUpdater.")
