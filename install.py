import argparse
import logging
import os
import pickle
import platform
import psycopg
import secrets
import shutil
import traceback
import sys

if sys.platform == 'win32':
    import winreg

from contextlib import closing, suppress
from core import utils, SAVED_GAMES
from pathlib import Path
from rich import print
from rich.prompt import IntPrompt, Prompt
from typing import Optional, Tuple
from urllib.parse import quote, urlparse

# ruamel YAML support
from ruamel.yaml import YAML
yaml = YAML()

DCSSB_DB_USER = "dcsserverbot"
DCSSB_DB_NAME = "dcsserverbot"


class InvalidParameter(Exception):
    def __init__(self, section: str, parameter: str, error: Optional[str] = None):
        if error:
            super().__init__(f"Section [{section}] has an invalid value for parameter \"{parameter}\": {error}")
        else:
            super().__init__(f"Section [{section}] has an invalid value for parameter \"{parameter}\".")


class MissingParameter(Exception):
    def __init__(self, section: str, parameter: str, error: Optional[str] = None):
        if error:
            super().__init__(f"Parameter \"{parameter}\" missing in section [{section}]: {error}")
        else:
            super().__init__(f"Parameter \"{parameter}\" missing in section [{section}]")


class Install:

    def __init__(self, node: str):
        self.node = node
        self.log = logging.getLogger(name='dcsserverbot')
        self.log.setLevel(logging.DEBUG)
        formatter = logging.Formatter(fmt=u'%(asctime)s.%(msecs)03d %(levelname)s\t%(message)s',
                                      datefmt='%Y-%m-%d %H:%M:%S')
        os.makedirs('logs', exist_ok=True)
        fh = logging.FileHandler(os.path.join('logs', f'{self.node}-install.log'), encoding='utf-8')
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(formatter)
        self.log.addHandler(fh)
        self.log.info("Installation started.")

    @staticmethod
    def get_dcs_installation_linux() -> Optional[str]:
        dcs_installation = None
        while dcs_installation is None:
            dcs_installation = Prompt.ask(prompt="Please enter the path to your DCS World installation")
            if not os.path.exists(dcs_installation):
                print("Directory not found. Please try again.")
                dcs_installation = None
        return dcs_installation

    @staticmethod
    def get_dcs_installation_win32() -> Optional[str]:
        print("\nSearching for DCS installations ...")
        key = skey = None
        try:
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Eagle Dynamics", 0)
            num_dcs_installs = winreg.QueryInfoKey(key)[0]
            if num_dcs_installs == 0:
                raise FileNotFoundError
            installs = list[Tuple[str, str]]()
            for i in range(0, num_dcs_installs):
                name = winreg.EnumKey(key, i)
                skey = winreg.OpenKey(winreg.HKEY_CURRENT_USER, f"Software\\Eagle Dynamics\\{name}", 0)
                path = winreg.QueryValueEx(skey, 'Path')[0]
                if os.path.exists(path):
                    installs.append((name, path))
            if len(installs) == 0:
                raise FileNotFoundError
            else:
                installs.append(("Custom", ""))
                print('I\'ve found multiple installations of DCS World on this PC:')
                for i in range(0, len(installs)):
                    print(f'{i+1}: {installs[i][0]}')
                num = IntPrompt.ask(prompt='Please specify, which installation you want the bot to use',
                                    choices=[str(x) for x in range(1, len(installs) + 1)],
                                    show_choices=True)
                path = installs[num-1][1]
                if not path:
                    raise FileNotFoundError
                return path
        except (FileNotFoundError, OSError):
            return Install.get_dcs_installation_linux()
        finally:
            if key:
                key.Close()
            if skey:
                skey.Close()

    @staticmethod
    def get_database_host(host: str = '127.0.0.1', port: int = 5432) -> Optional[Tuple[str, int]]:
        if not utils.is_open(host, port):
            print(f'[red]No PostgreSQL-database found on {host}:{port}![/]')
            host = Prompt.ask("Enter the hostname of your PostgreSQL-database", default='127.0.0.1')
            while not utils.is_open(host, port):
                port = IntPrompt.ask(prompt='Enter the port to your PostgreSQL-database', default=5432)
        return host, port

    @staticmethod
    def get_database_url() -> Optional[str]:
        host, port = Install.get_database_host('127.0.0.1', 5432)
        while True:
            passwd = Prompt.ask('Please enter your PostgreSQL master password (user=postgres)', password=True)
            url = f'postgres://postgres:{quote(passwd)}@{host}:{port}/postgres?sslmode=prefer'
            try:
                with psycopg.connect(url, autocommit=True) as conn:
                    with closing(conn.cursor()) as cursor:
                        if os.path.exists('password.pkl'):
                            with open('password.pkl', 'rb') as f:
                                passwd = pickle.load(f)
                        else:
                            passwd = secrets.token_urlsafe(8)
                            try:
                                cursor.execute(f"CREATE USER {DCSSB_DB_USER} WITH ENCRYPTED PASSWORD '{passwd}'")
                            except psycopg.Error:
                                print(f'[yellow]Existing {DCSSB_DB_USER} user found![/]')
                                while True:
                                    passwd = Prompt.ask(f"Please enter your password for user '{DCSSB_DB_USER}'",
                                                        password=True)
                                    try:
                                        with psycopg.connect(f"postgres://{DCSSB_DB_USER}:{quote(passwd)}@{host}:{port}/{DCSSB_DB_NAME}?sslmode=prefer"):
                                            pass
                                        break
                                    except psycopg.Error:
                                        print("[red]Wrong password! Try again.[/]")
                            with open('password.pkl', 'wb') as f:
                                pickle.dump(passwd, f)
                            with suppress(psycopg.Error):
                                cursor.execute(f"CREATE DATABASE {DCSSB_DB_NAME}")
                                cursor.execute(f"GRANT ALL PRIVILEGES ON DATABASE {DCSSB_DB_NAME} TO {DCSSB_DB_USER}")
                                cursor.execute(f"ALTER DATABASE {DCSSB_DB_NAME} OWNER TO {DCSSB_DB_USER}")
                            print("[green]- Database user and database created.[/]")
                        return f"postgres://{DCSSB_DB_USER}:{quote(passwd)}@{host}:{port}/{DCSSB_DB_NAME}?sslmode=prefer"
            except psycopg.OperationalError:
                print("[red]Master password wrong. Please try again.[/]")

    def install_master(self) -> Tuple[dict, dict, dict]:
        print("""
For a successful installation, you need to fulfill the following prerequisites:

    1. Installation of PostgreSQL from https://www.enterprisedb.com/downloads/postgres-postgresql-downloads
    2. A Discord TOKEN for your bot from https://discord.com/developers/applications

        """)
        if Prompt.ask(prompt="Have you fulfilled all these requirements", choices=['y', 'n'], show_choices=True,
                      default='n') == 'n':
            print("Aborting.")
            self.log.warning("Aborted: missing requirements")
            exit(-2)

        print("\n1. General Setup")
        # check if we can enable autoupdate
        autoupdate = Prompt.ask("Do you want your DCSServerBot being auto-updated?", choices=['y', 'n'],
                                default='y') == 'y'
        print("\n2. Discord Setup")
        guild_id = IntPrompt.ask(
            'Please enter your Discord Guild ID (right click on your Discord server, "Copy Server ID")')
        main = {
            "guild_id": guild_id,
            "autoupdate": autoupdate
        }
        token = Prompt.ask('Please enter your discord TOKEN (see documentation)') or '<see documentation>'
        owner = Prompt.ask('Please enter your Owner ID (right click on your discord user, "Copy User ID")')
        print("""
We now need to setup your Discord roles and channels.
DCSServerBot creates a role mapping for your bot users. It has the following internal roles:
        """)
        print({
            "Admin": "Users can delete data, change the bot, run commands on your server",
            "DCS Admin": "Users can upload missions, start/stop DCS servers, kick/ban users, etc.",
            "DCS": "Normal user, can pull statistics, ATIS, etc."
        })
        print("""
Please separate roles by comma, if you want to provide more than one.
You can keep the defaults, if unsure and create the respective roles in your Discord server.
        """)
        roles = {
            "Admin": Prompt.ask("Which role(s) in your discord should hold the [bold]Admin[/] role?",
                                default="Admin").split(','),
            "DCS Admin": Prompt.ask("Which role(s) in your discord should hold the [bold]DCS Admin[/] role?",
                                    default="DCS Admin").split(','),
            "DCS": Prompt.ask("Which role(s) in your discord should hold the [bold]DCS[/] role?",
                              default="@everyone").split(',')
        }
        bot = {
            "token": token,
            "owner": owner,
            "roles": roles
        }
        audit_channel = IntPrompt.ask("\nPlease provide a channel ID for audit events (optional) ", default=-1)
        admin_channel = IntPrompt.ask("\nThe bot can either use a dedicated admin channel for each server or a central "
                                      "admin channel for all servers.\n"
                                      "If you want to use a central one, please provide the ID (optional)", default=-1)
        if audit_channel and audit_channel != -1:
            bot['audit_channel'] = audit_channel
        if admin_channel and admin_channel != -1:
            bot['admin_channel'] = admin_channel
        nodes = {}
        return main, nodes, bot

    def install(self):
        major_version = int(platform.python_version_tuple()[1])
        if major_version <= 8 or major_version >= 12:
            print(f"""
[red]!!! Your Python 3.{major_version} installation is not supported, you might face issues. Please use 3.9 - 3.11 !!![/]
            """)
        print("""
[bright_blue]Hello! Thank you for choosing DCSServerBot.[/]
DCSServerBot supports everything from single server installations to huge server farms with multiple servers across 
the planet.

I will now guide you through the installation process.
If you need any further assistance, please visit the support discord, listed in the documentation.

        """)
        if not os.path.exists('config/main.yaml'):
            main, nodes, bot = self.install_master()
            master = True
            servers = {}
            schedulers = {}
            i = 2
        else:
            main = yaml.load(Path('config/main.yaml').read_text(encoding='utf-8'))
            nodes = yaml.load(Path('config/nodes.yaml').read_text(encoding='utf-8'))
            bot = yaml.load(Path('config/services/bot.yaml').read_text(encoding='utf-8'))
            try:
                servers = yaml.load(Path('config/servers.yaml').read_text(encoding='utf-8'))
            except FileNotFoundError:
                servers = {}
            try:
                schedulers = yaml.load(Path('config/plugins/schedulers.yaml').read_text(encoding='utf-8'))
            except FileNotFoundError:
                schedulers = {}
            if self.node in nodes:
                if Prompt.ask("[red]A configuration for this nodes exists already![/]\n"
                              "Do you want to overwrite it?", choices=['y', 'n'], default='n') == 'n':
                    print("Aborted.")
                    self.log.warning("Aborted: configuration exists")
                    exit(-1)
            else:
                print("[yellow]Configuration found, adding another node...[/]")
            master = False
            i = 0

        print(f"\n{i+1}. Database Setup")
        if master:
            database_url = Install.get_database_url()
            if not database_url:
                self.log.error("Aborted: No valid Database URL provided.")
                exit(-1)
        else:
            try:
                database_url = next(node['database']['url'] for node in nodes.values() if node.get('database'))
                url = urlparse(database_url)
                hostname, port = self.get_database_host(url.hostname, url.port)
                database_url = f"{url.scheme}://{url.username}:{url.password}@{hostname}:{port}{url.path}?sslmode=prefer"
            except StopIteration:
                database_url = self.get_database_url()

        print(f"\n{i+2}. Node Setup")
        if sys.platform == 'win32':
            dcs_installation = self.get_dcs_installation_win32() or '<see documentation>'
        else:
            dcs_installation = self.get_dcs_installation_linux()
        if not dcs_installation:
            self.log.error("Aborted: No DCS installation found.")
            exit(-1)
        node = nodes[self.node] = {
            "listen_port": max([n.get('listen_port', 10041 + idx) for idx, n in enumerate(nodes.values())]) + 1 if nodes else 10042,
            "DCS": {
                "installation": dcs_installation
            },
            "database": {
                "url": database_url
            }
        }
        if Prompt.ask("Do you want your DCS installation being auto-updated by the bot?", choices=['y', 'n'],
                      default='y') == 'y':
            node["DCS"]["autoupdate"] = True
        # Check for SRS
        srs_path = os.path.expandvars('%ProgramFiles%\\DCS-SimpleRadio-Standalone')
        if not os.path.exists(srs_path):
            srs_path = Prompt.ask("Please enter the path to your DCS-SRS installation.\n"
                                  "Press ENTER, if there is none.")
        if srs_path:
            self.log.info(f"DCS-SRS installation path: {srs_path}")
            node['extensions'] = {
                'SRS': {
                    'installation': srs_path
                }
            }
        else:
            self.log.info("- DCS-SRS not configured.")

        print(f"\n{i+3}. DCS Server Setup")
        scheduler = schedulers[self.node] = {}
        node['instances'] = {}
        # calculate unique bot ports
        bot_port = max([
            i.get('bot_port', 6665 + idx)
            for idx, i in enumerate([n.get('instances', []) for n in nodes.values()])
        ]) + 1 if nodes else 6666
        # calculate unique SRS ports
        srs_port = max([
            i.get('extensions', {}).get('SRS', {}).get('port', 5001 + idx)
            for idx, i in enumerate([n.get('instances', []) for n in nodes.values()])
        ]) + 1 if nodes else 5002
        instances = utils.findDCSInstances()
        if not instances == 0:
            print("There are no DCS servers installed yet.")
        for name, instance in instances:
            if Prompt.ask(f'\nDCS server "{name}" found.\n'
                          'Would you like to manage this server through DCSServerBot?)',
                          choices=['y', 'n'], show_choices=True, default='y') == 'y':
                self.log.info(f"Adding instance {instance} with server {name} ...")
                node['instances'][instance] = {
                    "bot_port": bot_port,
                    "home": os.path.join(SAVED_GAMES, instance)
                }
                if srs_path:
                    srs_config = f"%USERPROFILE%\\Saved Games\\{instance}\\Config\\SRS.cfg"
                    node['instances'][instance]['extensions'] = {
                        "SRS": {
                            "config": srs_config,
                            "port": srs_port
                        }
                    }
                    if not os.path.exists(os.path.expandvars(srs_config)):
                        if os.path.exists(os.path.join(srs_path, "server.cfg")):
                            shutil.copy2(os.path.join(srs_path, "server.cfg"), os.path.expandvars(srs_config))
                        else:
                            print("[red]SRS configuration could not be created.\n"
                                  f"Please copy your server.cfg to {srs_config} manually.[/]")
                            self.log.warning("SRS configuration could not be created, manual setup necessary.")
                bot_port += 1
                srs_port += 2
                print("DCSServerBot needs up to 3 channels per supported server:")
                print({
                    "Status Channel": "To display the mission and player status.",
                    "Chat Channel": "[bright_black]Optional:[/]: An in-game chat replication.",
                    "Admin Channel": "[bright_black]Optional:[/] For admin commands. Only needed, "
                                     "if no central admin channel is set."
                })
                print("""
The Status Channel should be readable by everyone and only writable by the bot.
The Chat Channel should be readable and writable by everyone.
The Admin channel - if provided - should only be readable and writable by Admin and DCS Admin users.

You can create these channels now, as I will ask for the IDs in a bit. 
DCSServerBot needs the following permissions on them to work:

    - View Channel
    - Send Messages
    - Read Messages
    - Read Message History
    - Add Reactions
    - Attach Files
    - Embed Links
    - Manage Messages
                """)

                servers[name] = {
                    "channels": {
                        "status": IntPrompt.ask("Please enter the ID of your [bold]Status Channel[/]"),
                        "chat": IntPrompt.ask("Please enter the ID of your [bold]Chat Channel[/] (optional)",
                                              default=-1)
                    }
                }
                if 'admin_channel' not in bot:
                    servers[name]['channels']['admin'] = IntPrompt.ask("Please enter the ID of your admin channel")
                if Prompt.ask("Do you want DCSServerBot to autostart this server?", choices=['y', 'n'],
                              default='y') == 'y':
                    scheduler[instance] = {
                        "schedule": {
                            "00-24": "YYYYYYY"
                        }
                    }
                else:
                    scheduler[instance] = {}
                self.log.info(f"Instance {instance} added.")
        print("\n\nAll set. Writing / updating your config files now...")
        if master:
            with open('config/main.yaml', 'w', encoding='utf-8') as out:
                yaml.dump(main, out)
                print("- Created config/main.yaml")
            self.log.info("./config/main.yaml written.")
            os.makedirs('config/services', exist_ok=True)
            with open('config/services/bot.yaml', 'w', encoding='utf-8') as out:
                yaml.dump(bot, out)
                print("- Created config/services/bot.yaml")
            self.log.info("./config/services/bot.yaml written.")
        with open('config/nodes.yaml', 'w', encoding='utf-8') as out:
            yaml.dump(nodes, out)
            if os.path.exists('password.pkl'):
                os.remove('password.pkl')
            print("- Created config/nodes.yaml")
        self.log.info("./config/nodes.yaml written.")
        with open('config/servers.yaml', 'w', encoding='utf-8') as out:
            yaml.dump(servers, out)
            print("- Created config/servers.yaml")
        self.log.info("./config/servers.yaml written.")
        # write plugin configuration
        if scheduler:
            os.makedirs('config/plugins', exist_ok=True)
            with open('config/plugins/scheduler.yaml', 'w', encoding='utf-8') as out:
                yaml.dump(schedulers, out)
                print("- Created config/plugins/scheduler.yaml")
            self.log.info("./config/plugins/scheduler.yaml written.")
        print("""
[green]Your basic DCSServerBot configuration is finished.[/]
 
You can now review the created configuration files below your config folder of your DCSServerBot-installation.
There is much more to explore and to configure, so please don't forget to have a look at the documentation!

You can start DCSServerBot with:

    [bright_black]run.cmd[/]
        """)
        self.log.info("Installation finished.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog='DCSServerBot', description="Welcome to DCSServerBot!",
                                     epilog='If unsure about the parameters, please check the documentation.')
    parser.add_argument('-n', '--node', help='Node name', default=platform.node())
    args = parser.parse_args()
    try:
        Install(args.node).install()
    except KeyboardInterrupt:
        pass
    except Exception:
        traceback.print_exc()
    print("\nAborted.")
