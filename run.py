from __future__ import annotations

import asyncio
import discord
import logging
import os
import pathlib
import platform
import psycopg
import sys
import time

from core import (
    NodeImpl, ServiceRegistry, ServiceInstallationError, utils, YAMLError, FatalException, COMMAND_LINE_ARGS,
    CloudRotatingFileHandler
)
from datetime import datetime
from install import Install
from migrate import migrate
from pid import PidFile, PidFileError
from rich import print
from rich.console import Console
from rich.logging import RichHandler
from rich.text import Text

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

LOGLEVEL = {
    'DEBUG': logging.DEBUG,
    'INFO': logging.INFO,
    'WARNING': logging.WARNING,
    'ERROR': logging.ERROR,
    'CRITICAL': logging.CRITICAL,
    'FATAL': logging.FATAL
}


class Main:

    def __init__(self, node: NodeImpl, no_autoupdate: bool) -> None:
        self.node = node
        self.log = logging.getLogger(__name__)
        self.no_autoupdate = no_autoupdate
        utils.dynamic_import('services')

    @staticmethod
    def setup_logging(node: str):
        def time_formatter(time: datetime, _: str = None) -> Text:
            return Text(time.strftime('%H:%M:%S'))

        # Setup console logger
        ch = RichHandler(rich_tracebacks=True, tracebacks_suppress=[discord], log_time_format=time_formatter)
        ch.setLevel(logging.INFO)

        # Setup file logging
        try:
            config = yaml.load(pathlib.Path('config/main.yaml').read_text(encoding='utf-8'))['logging']
        except (FileNotFoundError, KeyError, YAMLError):
            config = {}
        os.makedirs('logs', exist_ok=True)
        fh = CloudRotatingFileHandler(os.path.join('logs', f'dcssb-{node}.log'), encoding='utf-8',
                                      maxBytes=config.get('logrotate_size', 10485760),
                                      backupCount=config.get('logrotate_count', 5))
        fh.setLevel(logging.DEBUG)
        formatter = logging.Formatter(fmt=u'%(asctime)s.%(msecs)03d %(levelname)s\t%(message)s',
                                      datefmt='%Y-%m-%d %H:%M:%S')
        formatter.converter = time.gmtime
        fh.setFormatter(formatter)
        fh.doRollover()

        # Configure the root logger
        logging.basicConfig(level=LOGLEVEL[config.get('loglevel', 'DEBUG')], format="%(message)s", handlers=[ch, fh])

        # Change 3rd-party logging
        logging.getLogger(name='asyncio').setLevel(logging.WARNING)
        logging.getLogger(name='discord').setLevel(logging.ERROR)
        logging.getLogger(name='git').setLevel(logging.WARNING)
        logging.getLogger(name='matplotlib').setLevel(logging.ERROR)
        logging.getLogger(name='PidFile').setLevel(logging.ERROR)
        logging.getLogger(name='psycopg.pool').setLevel(logging.WARNING)
        logging.getLogger(name='pykwalify').setLevel(logging.CRITICAL)

        # Performance logging
        perf_logger = logging.getLogger(name='performance_log')
        perf_logger.setLevel(logging.DEBUG)
        perf_logger.propagate = False
        pfh = CloudRotatingFileHandler(os.path.join('logs', f'perf-{node}.log'), encoding='utf-8',
                                       maxBytes=config.get('logrotate_size', 10485760),
                                       backupCount=config.get('logrotate_count', 5))
        pff = logging.Formatter('%(asctime)s\t%(levelname)s\t%(message)s')
        pff.converter = time.gmtime
        pfh.setFormatter(pff)
        pfh.doRollover()
        perf_logger.addHandler(pfh)

    @staticmethod
    def reveal_passwords(config_dir: str):
        print("[yellow]These are your hidden secrets:[/]")
        for file in utils.list_all_files(os.path.join(config_dir, '.secret')):
            if not file.endswith('.pkl'):
                continue
            key = file[:-4]
            print(f"{key}: {utils.get_password(key, config_dir)}")
        print("\n[red]DO NOT SHARE THESE SECRET KEYS![/]")

    async def run(self):
        await self.node.post_init()
        # check for updates
        if self.no_autoupdate:
            autoupdate = False
            # remove the exec parameter, to allow restart/update of the node
            if '--x' in sys.argv:
                sys.argv.remove('--x')
            elif '--noupdate' in sys.argv:
                sys.argv.remove('--noupdate')
        else:
            autoupdate = self.node.locals.get('autoupdate', self.node.config.get('autoupdate', False))

        if autoupdate:
            cloud_drive = self.node.locals.get('cloud_drive', True)
            if (cloud_drive and self.node.master) or not cloud_drive:
                await self.node.upgrade()
        elif self.node.master and await self.node.upgrade_pending():
            self.log.warning(
                "New update for DCSServerBot available!\nUse /node upgrade or enable autoupdate to apply it.")

        await self.node.register()
        async with ServiceRegistry(node=self.node) as registry:
            if registry.services():
                self.log.info("- Loading Services ...")
            services = [registry.new(cls) for cls in registry.services().keys() if registry.can_run(cls)]
            ret = await asyncio.gather(*[service.start() for service in services], return_exceptions=True)
            for idx in range(0, len(ret)):
                name = services[idx].name
                if isinstance(ret[idx], (ServiceInstallationError, FatalException)):
                    self.log.error(f"  - {ret[idx].__str__()}")
                    self.log.error(f"  => Service {name} NOT started.")
                    if isinstance(ret[idx], FatalException):
                        return
                else:
                    self.log.debug(f"  => Service {name} started.")
            if not self.node.master:
                self.log.info("DCSServerBot AGENT started.")
            try:
                while True:
                    # wait until the master changes
                    while self.node.master == await self.node.heartbeat():
                        if self.node.is_shutdown.is_set():
                            return
                        await asyncio.sleep(5)
                    # switch master
                    self.node.master = not self.node.master
                    if self.node.master:
                        self.log.info("Taking over the Master node ...")
                        for cls in registry.services().keys():
                            if registry.master_only(cls):
                                try:
                                    await registry.new(cls).start()
                                except ServiceInstallationError as ex:
                                    self.log.error(f"  - {ex.__str__()}")
                                    self.log.error(f"  => {cls.__name__} NOT loaded.")
                            else:
                                service = registry.get(cls)
                                if service:
                                    await service.switch()
                    else:
                        self.log.info("Second Master found, stepping back to Agent configuration.")
                        for cls in registry.services().keys():
                            if registry.master_only(cls):
                                await registry.get(cls).stop()
                            else:
                                service = registry.get(cls)
                                if service:
                                    await service.switch()
                    self.log.info(f"I am the {'Master' if self.node.master else 'Agent'} now.")
            except Exception as ex:
                self.log.exception(ex)
                self.log.warning("Aborting the main loop.")
                raise
            finally:
                await self.node.unregister()


async def run_node(name, config_dir=None, no_autoupdate=False) -> int:
    async with NodeImpl(name=name, config_dir=config_dir) as node:
        await Main(node, no_autoupdate=no_autoupdate).run()
        return node.rc


if __name__ == "__main__":
    console = Console()

    if sys.platform == 'win32':
        # disable quick edit mode (thanks to Moots)
        utils.quick_edit_mode(False)
        # set the asyncio event loop policy
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

    # get the command line args from core
    args = COMMAND_LINE_ARGS

    # Setup the logging
    Main.setup_logging(args.node)
    log = logging.getLogger("dcsserverbot")
    # check if we should reveal the passwords
    utils.create_secret_dir(args.config)
    if args.secret:
        Main.reveal_passwords(args.config)
        exit(-2)

    # Check versions
    if int(platform.python_version_tuple()[0]) < 3 or int(platform.python_version_tuple()[1]) < 9:
        log.error("You need Python 3.9 or higher to run DCSServerBot (3.11 recommended)!")
        exit(-2)
    elif int(platform.python_version_tuple()[1]) == 9:
        log.warning("Python 3.9 is outdated, you should consider upgrading it to 3.10 or higher.")

    # Call the DCSServerBot 2.x migration utility
    if os.path.exists(os.path.join(args.config, 'dcsserverbot.ini')):
        migrate(node=args.node)
    try:
        with PidFile(pidname=f"dcssb_{args.node}", piddir='.'):
            try:
                rc = asyncio.run(run_node(name=args.node, config_dir=args.config, no_autoupdate=args.noupdate))
            except FatalException:
                Install(node=args.node).install(config_dir=args.config, user='dcsserverbot', database='dcsserverbot')
                rc = asyncio.run(run_node(name=args.node, config_dir=args.config, no_autoupdate=args.noupdate))
    except PermissionError:
        # do not restart again
        log.error("There is a permission error.")
        log.error(f"Did you run DCSServerBot as Admin before? If yes, delete dcssb_{args.node}.pid and try again.")
        exit(-2)
    except PidFileError:
        log.error(f"Process already running for node {args.node}!")
        log.error(f"If you are sure there is no 2nd process running, delete dcssb_{args.node}.pid and try again.")
        # do not restart again
        exit(-2)
    except KeyboardInterrupt:
        # restart again (old handling)
        exit(-1)
    except asyncio.CancelledError:
        # do not restart again
        exit(-2)
    except (YAMLError, FatalException) as ex:
        log.exception(ex)
        input("Press any key to continue ...")
        # do not restart again
        exit(-2)
    except psycopg.OperationalError as ex:
        log.error(f"Database Error: {ex}", exc_info=True)
        input("Press any key to continue ...")
        # do not restart again
        exit(-2)
    except SystemExit as ex:
        exit(ex.code)
    except:
        console.print_exception(show_locals=True, max_frames=1)
        # restart on unknown errors
        exit(-1)
    exit(rc)
