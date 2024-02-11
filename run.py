from __future__ import annotations

import argparse
import asyncio
import os
import platform
import traceback

from core import NodeImpl, ServiceRegistry, ServiceInstallationError, YAMLError, FatalException
from install import Install
from migrate import migrate
from pid import PidFile, PidFileError

# Register all services
import services


class Main:

    def __init__(self, node: NodeImpl, no_autoupdate: bool) -> None:
        self.node = node
        self.log = node.log
        self.no_autoupdate = no_autoupdate

    async def run(self):
        await self.node.post_init()
        # check for updates
        if self.no_autoupdate:
            autoupdate = False
        else:
            autoupdate = self.node.locals.get('autoupdate', self.node.config.get('autoupdate', False))

        if autoupdate:
            cloud_drive = self.node.locals.get('cloud_drive', True)
            if (cloud_drive and self.node.master) or not cloud_drive:
                await self.node.upgrade()
        elif await self.node.upgrade_pending():
            self.log.warning("There is a new update for DCSServerBot available!")

        await self.node.register()
        async with ServiceRegistry(node=self.node) as registry:
            if registry.services():
                self.log.info("- Loading Services ...")
            for name in registry.services().keys():
                if not registry.can_run(name):
                    continue
                if name == 'Dashboard':
                    if self.node.config.get('use_dashboard', True):
                        self.log.info("  => Dashboard started.")
                        dashboard = registry.new(name)
                        asyncio.create_task(dashboard.start())
                    continue
                else:
                    try:
                        asyncio.create_task(registry.new(name).start())
                        self.log.debug(f"  => {name} loaded.")
                    except ServiceInstallationError as ex:
                        self.log.error(f"  - {ex.__str__()}")
                        self.log.info(f"  => {name} NOT loaded.")
            if not self.node.master:
                self.log.info("DCSServerBot AGENT started.")
            try:
                while True:
                    # wait until the master changes
                    while self.node.master == await self.node.heartbeat():
                        await asyncio.sleep(5)
                    # switch master
                    self.node.master = not self.node.master
                    if self.node.master:
                        self.log.info("Taking over the Master node ...")
                        if self.node.config.get('use_dashboard', True):
                            await dashboard.stop()
                        for name in registry.services().keys():
                            if registry.master_only(name):
                                try:
                                    asyncio.create_task(registry.new(name).start())
                                except ServiceInstallationError as ex:
                                    self.log.error(f"  - {ex.__str__()}")
                                    self.log.info(f"  => {name} NOT loaded.")
                    else:
                        self.log.info("Second Master found, stepping back to Agent configuration.")
                        if self.node.config.get('use_dashboard', True):
                            await dashboard.stop()
                        for name in registry.services().keys():
                            if registry.master_only(name):
                                await registry.get(name).stop()
                    if self.node.config.get('use_dashboard', True):
                        await dashboard.start()
                    self.log.info(f"I am the {'MASTER' if self.node.master else 'AGENT'} now.")
            finally:
                await self.node.unregister()


if __name__ == "__main__":
    if int(platform.python_version_tuple()[0]) < 3 or int(platform.python_version_tuple()[1]) < 9:
        print("You need Python 3.9 or higher to run DCSServerBot (3.11 recommended)!")
        exit(-2)
    elif int(platform.python_version_tuple()[1]) == 9:
        print("Python 3.9 is outdated, you should consider upgrading it to 3.10 or higher.")

    parser = argparse.ArgumentParser(prog='run.py', description="Welcome to DCSServerBot!",
                                     epilog='If unsure about the parameters, please check the documentation.')
    parser.add_argument('-n', '--node', help='Node name', default=platform.node())
    parser.add_argument('-x', '--noupdate', action='store_true', help='Do not autoupdate')
    args = parser.parse_args()
    # Call the DCSServerBot 2.x migration utility
    if os.path.exists('config/dcsserverbot.ini'):
        migrate(node=args.node)
    try:
        with PidFile(pidname=f"dcssb_{args.node}"):
            try:
                node = NodeImpl(name=args.node)
            except FatalException:
                Install(node=args.node).install()
                node = NodeImpl(name=args.node)
            asyncio.run(Main(node, no_autoupdate=args.noupdate).run())
    except PermissionError:
        exit(-2)
    except PidFileError:
        print(f"Process already running for node {args.node}! Exiting...")
        exit(-2)
    except KeyboardInterrupt:
        # restart again (old handling)
        exit(-1)
    except asyncio.CancelledError:
        # do not restart again
        exit(-2)
    except (YAMLError, FatalException) as ex:
        print(ex)
        # do not restart again
        exit(-2)
    except SystemExit as ex:
        exit(ex.code)
    except:
        traceback.print_exc()
        # restart on unknown errors
        exit(-1)
