import asyncio
import atexit
import hashlib
import json
import os
import psutil
import stat
import subprocess
import sys

from core import Extension, utils, Server, get_translation
from threading import Thread
from typing import Optional

_ = get_translation(__name__.split('.')[1])

server_ports: dict[int, str] = dict()
client_ports: dict[int, str] = dict()

__all__ = [
    "Olympus"
]


class Olympus(Extension):

    CONFIG_DICT = {
        "backend_port": {
            "type": int,
            "label": _("Backend Port"),
            "placeholder": _("Backend Port"),
            "required": True
        },
        "gameMasterPassword": {
            type: str,
            "label": _("Game Master Password")
        },
        "blueCommanderPassword": {
            type: str,
            "label": _("Blue Commander Password")
        },
        "redCommanderPassword": {
            type: str,
            "label": _("Red Commander Password")
        },
        "frontend_port": {
            "type": int,
            "label": _("Frontend Port"),
            "placeholder": _("Frontend Port"),
            "required": True
        }
    }

    def __init__(self, server: Server, config: dict):
        self.home = os.path.join(server.instance.home, 'Mods', 'Services', 'Olympus')
        super().__init__(server, config)
        self.nodejs = os.path.join(os.path.expandvars(self.config.get('nodejs', '%ProgramFiles%\\nodejs')), 'node.exe')
        # check if there is an olympus process running already
        self.process: Optional[psutil.Process] = utils.find_process(os.path.basename(self.nodejs),
                                                                    self.server.instance.name)
        if self.process:
            self.log.debug("- Running Olympus process found.")

        if self.version == '1.0.3.0':
            self.backend_tag = 'server'
            self.frontend_tag = 'client'
        else:
            self.backend_tag = 'backend'
            self.frontend_tag = 'frontend'

    @property
    def name(self) -> str:
        return "DCS Olympus"

    @property
    def version(self) -> Optional[str]:
        return utils.get_windows_version(os.path.join(self.home, 'bin', 'olympus.dll'))

    @property
    def config_path(self) -> str:
        if self.version == '1.0.3.0':
            return os.path.join(self.home, 'olympus.json')
        else:
            return os.path.join(self.server.instance.home, 'Config', 'olympus.json')

    def load_config(self) -> Optional[dict]:
        try:
            with open(self.config_path, mode='r', encoding='utf-8') as file:
                return json.load(file)
        except Exception:
            self.log.warning(f"{self.name}: Config file not found or corrupt, using defaults")
            elevation_provider = {
                "provider": "https://srtm.fasma.org/{lat}{lng}.SRTMGL3S.hgt.zip",
                "username": None,
                "password": None
            }
            backend = {
                "address": "localhost",
                "port": 3001
            }
            frontend = {
                "port": 3000,
                "elevationProvider": elevation_provider
            }
            if self.version == '1.0.3.0':
                return {
                    "server": backend,
                    "client": frontend
                }
            else:
                return {
                    "backend": backend,
                    "frontend": frontend
                }

    def is_installed(self) -> bool:
        if not super().is_installed():
            return False
        if not os.path.exists(os.path.join(self.home, 'bin', 'olympus.dll')):
            self.log.warning(f"  => {self.server.name}: Can't load extension, {self.name} is not installed!")
            return False
        if not os.path.exists(self.nodejs):
            self.log.warning(f"  => {self.server.name}: Can't run {self.name}, Node.js is not installed!")
            return False
        return True

    async def render(self, param: Optional[dict] = None) -> dict:
        if 'url' in self.config:
            value = self.config['url']
        else:
            value = f"http://{self.node.public_ip}:{self.config.get(self.frontend_tag, {}).get('port', 3000)}"
        if self.config.get('show_passwords', False):
            value += ''.join([
                f"\n{x[0].upper() + x[1:]}: {self.config.get('authentication', {}).get(f'{x}Password', '')}"
                for x in ['gameMaster', 'blueCommander', 'redCommander']
            ])
        return {
            "name": self.__class__.__name__,
            "version": self.version,
            "value": value
        }

    async def prepare(self) -> bool:
        global server_ports, client_ports

        if not self.is_installed():
            return False
        self.log.debug(f"Preparing {self.name} configuration ...")
        try:
            try:
                os.chmod(self.config_path, stat.S_IWUSR)
            except FileNotFoundError:
                pass
            except PermissionError:
                self.log.warning(
                    f"  => {self.server.name}: No write permission on olympus.json, skipping {self.name}.")
                return False
            server_port = self.config.get(self.backend_tag, {}).get('port', 3001)
            if server_ports.get(server_port, self.server.name) != self.server.name:
                self.log.error(f"  => {self.server.name}: {self.name} server.port {server_port} already in use by "
                               f"server {server_ports[server_port]}!")
                return False
            server_ports[server_port] = self.server.name
            client_port = self.config.get(self.frontend_tag, {}).get('port', 3000)
            if client_ports.get(client_port, self.server.name) != self.server.name:
                self.log.error(f"  => {self.server.name}: {self.name} client.port {client_port} already in use by "
                               f"server {client_ports[client_port]}!")
                return False
            client_ports[client_port] = self.server.name

            self.locals = self.load_config()
            default_address = '*' if self.version == '1.0.3.0' else 'localhost'
            self.locals[self.backend_tag]['address'] = self.config.get(self.backend_tag, {}).get('address', default_address)
            self.locals[self.backend_tag]['port'] = server_port
            self.locals[self.frontend_tag]['port'] = client_port
            self.locals['authentication'] = {
                "gameMasterPassword": hashlib.sha256(
                    str(self.config.get('authentication', {}).get('gameMasterPassword', '')).encode('utf-8')).hexdigest(),
                "blueCommanderPassword": hashlib.sha256(
                    str(self.config.get('authentication', {}).get('blueCommanderPassword', '')).encode('utf-8')).hexdigest(),
                "redCommanderPassword": hashlib.sha256(
                    str(self.config.get('authentication', {}).get('redCommanderPassword', '')).encode('utf-8')).hexdigest()
            }
            with open(self.config_path, 'w', encoding='utf-8') as cfg:
                json.dump(self.locals, cfg, indent=2)

            return await super().prepare()
        except Exception as ex:
            self.log.error(f"Error during launch of {self.name}: {str(ex)}")
            return False

    async def startup(self) -> bool:
        await super().startup()

        def log_output(proc: subprocess.Popen):
            for line in iter(proc.stdout.readline, b''):
                self.log.debug(line.decode('utf-8').rstrip())

        def run_subprocess():
            out = subprocess.PIPE if self.config.get('debug', False) else subprocess.DEVNULL
            path = os.path.expandvars(
                self.config.get('frontend', {}).get('path', os.path.join(self.home, self.frontend_tag)))
            if not os.path.exists(os.path.join(path, 'bin', 'www')):
                self.log.error(f"Path {os.path.join(path, 'bin', 'www')} does not exist, can't launch Olympus!")
                return
            args = [self.nodejs, os.path.join(path, 'bin', 'www')]
            if self.version != '1.0.3.0':
                args.append('--config')
                args.append(self.config_path)
            self.log.debug("Launching {}".format(' '.join(args)))
            proc = subprocess.Popen(args, cwd=path, stdout=out, stderr=subprocess.STDOUT)
            if self.config.get('debug', False):
                Thread(target=log_output, args=(proc,), daemon=True).start()
            return proc

        try:
            p = await asyncio.to_thread(run_subprocess)
            try:
                self.process = psutil.Process(p.pid)
            except (AttributeError, psutil.NoSuchProcess):
                self.log.error(f"Failed to start Olympus server, enable debug in the extension.")
                return False
            atexit.register(self.terminate)
        except OSError as ex:
            self.log.error("Error while starting Olympus: " + str(ex))
            return False
        if sys.platform == 'win32':
            from os import system
            system(f"title DCSServerBot v{self.server.node.bot_version}.{self.server.node.sub_version}")
        # Give the Olympus server 10s to start
        for _ in range(0, 10):
            if self.is_running():
                return True
            await asyncio.sleep(1)
        return False

    def is_running(self) -> bool:
        return self.process is not None and self.process.is_running()

    def terminate(self) -> bool:
        try:
            utils.terminate_process(self.process)
            self.process = None
            return True
        except Exception as ex:
            self.log.error(f"Error during shutdown of {self.config['cmd']}: {str(ex)}")
            return False

    def shutdown(self) -> bool:
        super().shutdown()
        return self.terminate()
