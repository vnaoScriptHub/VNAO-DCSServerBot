import asyncio
import glob
import os
import shlex
import subprocess
import time
import sys

from core import ServiceRegistry, Service, utils
from datetime import datetime
from discord.ext import tasks
from typing import TYPE_CHECKING
from urllib.parse import urlparse
from zipfile import ZipFile

if TYPE_CHECKING:
    from .. import ServiceBus

__all__ = ["BackupService"]


@ServiceRegistry.register("Backup", plugin="backup")
class BackupService(Service):
    def __init__(self, node, name: str):
        super().__init__(node, name)
        if not self.locals:
            self.log.debug("  - No backup.yaml configured, skipping backup service.")
            return
        self.bus: ServiceBus = ServiceRegistry.get("ServiceBus")

    async def start(self):
        if not self.locals:
            return
        await super().start()
        self.schedule.start()
        delete_after = self.locals.get('delete_after', 'never')
        if isinstance(delete_after, int) or delete_after.isnumeric():
            self.delete.start()

    async def stop(self, *args, **kwargs):
        if not self.locals:
            return
        self.schedule.stop()
        delete_after = self.locals.get('delete_after', 'never')
        if isinstance(delete_after, int) or delete_after.isnumeric():
            self.delete.stop()

    def mkdir(self) -> str:
        target = os.path.expandvars(self.locals.get('target'))
        directory = os.path.join(target, utils.slugify(self.node.name) + '_' + datetime.now().strftime("%Y%m%d"))
        os.makedirs(directory, exist_ok=True)
        return directory

    @staticmethod
    def zip_path(zf: ZipFile, base: str, path: str):
        for root, dirs, files in os.walk(os.path.join(base, path)):
            for file in files:
                zf.write(os.path.join(root, file), os.path.join(root.replace(base, ''), file))

    def backup_bot(self) -> bool:
        self.log.info("Backing up DCSServerBot ...")
        target = self.mkdir()
        config = self.locals['backups'].get('bot')
        filename = "bot_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".zip"
        zf = ZipFile(os.path.join(target, filename), mode="w")
        try:
            for directory in config.get('directories', ['config', 'reports']):
                self.zip_path(zf, "", directory)
            self.log.info("Backup of DCSServerBot complete.")
            return True
        except Exception:
            self.log.error(f'Backup of DCSServerBot failed.', exc_info=True)
            return False
        finally:
            zf.close()

    def backup_servers(self) -> bool:
        target = self.mkdir()
        config = self.locals['backups'].get('servers')
        rc = True
        for server_name, server in self.bus.servers.items():
            self.log.info(f'Backing up server "{server_name}" ...')
            filename = f"{server.instance.name}_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".zip"
            zf = ZipFile(os.path.join(target, filename), mode="w")
            try:
                root_dir = server.instance.home
                for directory in config.get('directories', ['Config', 'Missions', 'Scripts']):
                    self.zip_path(zf, root_dir, directory)
                self.log.info(f'Backup of server "{server_name}" complete.')
            except Exception:
                self.log.error(f'Backup of server "{server_name}" failed.', exc_info=True)
                rc = False
            finally:
                zf.close()
        return rc

    def backup_database(self) -> bool:
        target = self.mkdir()
        config = self.locals['backups'].get('database')
        cmd = os.path.join(os.path.expandvars(config['path']), "pg_dump.exe")
        if not os.path.exists(cmd):
            raise FileNotFoundError(cmd)
        filename = f"db_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".tar"
        path = os.path.join(target, filename)
        url = self.node.config.get("database", self.node.locals.get('database'))['url']
        database = urlparse(url).path.strip('/')
        args = shlex.split(f'--no-owner --no-privileges -U postgres -F t -f "{path}" -d "{database}"')
        os.environ['PGPASSWORD'] = config['password']
        self.log.info("Backing up database...")
        process = subprocess.run([os.path.basename(cmd), *args], executable=cmd,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        rc = process.returncode
        if rc == 0:
            self.log.info("Backup of database complete.")
            return True
        else:
            self.log.info(f"Backup of database failed. Code: {rc}")
            return False

    def recover_database(self, date: str):
        target = os.path.expandvars(self.locals.get('target'))
        path = os.path.join(target, f"{self.node.name.lower()}_{date}", f"db_{date}_*.tar")
        filename = glob.glob(path)[0]
        os.execv(sys.executable, ['python', 'recover.py', '-n', self.node.name, '-f', filename])

    def recover_bot(self, filename: str):
        ...

    def recover_server(self, filename: str):
        ...

    @staticmethod
    def can_run(config: dict):
        if 'schedule' not in config:
            return False
        now = datetime.now()
        if utils.is_match_daystate(now, config['schedule']['days']):
            for _time in config['schedule']['times']:
                if utils.is_in_timeframe(now, _time):
                    return True
        return False

    @tasks.loop(minutes=1)
    async def schedule(self):
        tasks = []
        if self.node.master:
            if 'bot' in self.locals['backups'] and self.can_run(self.locals['backups']['bot']):
                tasks.append(asyncio.create_task(asyncio.to_thread(self.backup_bot)))
            if 'database' in self.locals['backups'] and self.can_run(self.locals['backups']['database']):
                tasks.append(asyncio.create_task(asyncio.to_thread(self.backup_database)))
        if 'servers' in self.locals['backups'] and self.can_run(self.locals['backups']['servers']):
            tasks.append(asyncio.create_task(asyncio.to_thread(self.backup_servers)))
        if tasks:
            await asyncio.gather(*tasks)
            self.log.info("Backup finished.")

    @tasks.loop(hours=24)
    async def delete(self):
        try:
            path = os.path.expandvars(self.locals['target'])
            if not os.path.exists(path):
                return
            delete_after = int(self.locals['delete_after'])
            threshold_time = time.time() - delete_after * 86400
            for file in os.listdir(path):
                file_path = os.path.join(path, file)
                if os.path.getctime(file_path) < threshold_time:
                    self.log.debug(f"  => {file} is older then {delete_after} days, deleting ...")
                    utils.safe_rmtree(file_path)
        except Exception as ex:
            self.log.exception(ex)
