from __future__ import annotations

import builtins
import importlib
import json
import time
import luadata
import os
import re
import shutil
import string
import tempfile
import unicodedata

# for eval
import random
import math

from croniter import croniter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Union, TYPE_CHECKING, Tuple, Generator, Iterable
from urllib.parse import urlparse

# ruamel YAML support
from ruamel.yaml import YAML
from ruamel.yaml.parser import ParserError
from ruamel.yaml.scanner import ScannerError

yaml = YAML()

if TYPE_CHECKING:
    from core import ServerProxy, DataObject

__all__ = [
    "is_in_timeframe",
    "is_match_daystate",
    "str_to_class",
    "format_string",
    "convert_time",
    "format_time",
    "get_utc_offset",
    "format_period",
    "slugify",
    "alternate_parse_settings",
    "get_all_servers",
    "get_all_players",
    "is_ucid",
    "get_presets",
    "get_preset",
    "is_valid_url",
    "is_github_repo",
    "matches_cron",
    "SettingsDict",
    "RemoteSettingsDict",
    "evaluate",
    "for_each",
    "YAMLError"
]


def is_in_timeframe(time: datetime, timeframe: str) -> bool:
    def parse_time(time_str: str) -> datetime:
        fmt, time_str = ('%H:%M', time_str.replace('24:', '00:')) \
            if time_str.find(':') > -1 else ('%H', time_str.replace('24', '00'))
        return datetime.strptime(time_str, fmt)

    pos = timeframe.find('-')
    if pos != -1:
        start_time = parse_time(timeframe[:pos])
        end_time = parse_time(timeframe[pos + 1:])
        if end_time <= start_time:
            end_time += timedelta(days=1)
    else:
        start_time = end_time = parse_time(timeframe)
    check_time = time.replace(year=start_time.year, month=start_time.month, day=start_time.day, second=0, microsecond=0)
    return start_time <= check_time <= end_time


def is_match_daystate(time: datetime, daystate: str) -> bool:
    state = daystate[time.weekday()]
    return state.upper() == 'Y'


def str_to_class(name: str):
    try:
        if '.' in name:
            module_name, class_name = name.rsplit('.', 1)
            module = importlib.import_module(module_name)
        else:
            class_name = name
            module = builtins
        return getattr(module, class_name)
    except AttributeError:
        return None


def format_string(string_: str, default_: Optional[str] = None, **kwargs) -> str:
    class NoneFormatter(string.Formatter):
        def format_field(self, value, spec):
            if value is None:
                spec = ''
                if default_:
                    value = default_
                else:
                    value = ""
            elif isinstance(value, list):
                value = '\n'.join(value)
            elif isinstance(value, dict):
                value = json.dumps(value)
            elif isinstance(value, bool):
                value = str(value).lower()
            return super().format_field(value, spec)

    try:
        string_ = NoneFormatter().format(string_, **kwargs)
    except KeyError:
        string_ = ""
    return string_


def convert_time(seconds: int):
    retval = ""
    days = int(seconds / 86400)
    if days != 0:
        retval += f"{days}d"
    seconds = seconds - days * 86400
    hours = int(seconds / 3600)
    if hours != 0:
        if len(retval):
            retval += ":"
        retval += f"{hours:02d}h"
    seconds = seconds - hours * 3600
    minutes = int(seconds / 60)
    if len(retval):
        retval += ":"
    retval += f"{minutes:02d}m"
    return retval


def format_time(seconds: int):
    retval = ""
    days = int(seconds / 86400)
    if days != 0:
        retval += f"{days} day"
        if days > 1:
            retval += "s"
        seconds -= days * 86400
    hours = int(seconds / 3600)
    if hours != 0:
        if len(retval):
            retval += " "
        retval += f"{hours} hour"
        if hours > 1:
            retval += "s"
        seconds -= hours * 3600
    minutes = int(seconds / 60)
    if minutes != 0:
        if len(retval):
            retval += " "
        retval += f"{minutes} minute"
        if minutes > 1:
            retval += "s"
        seconds -= minutes * 60
    if seconds != 0:
        if len(retval):
            retval += " "
        retval += f"{int(seconds)} second"
        if seconds > 1:
            retval += "s"
    return retval


def get_utc_offset() -> str:
    # Get the struct_time objects for the current local time and UTC time
    current_time = time.time()
    localtime = time.localtime(current_time)
    gmtime = time.gmtime(current_time)

    # Convert these to datetime objects
    local_dt = datetime(*localtime[:6], tzinfo=timezone.utc)
    utc_dt = datetime(*gmtime[:6], tzinfo=timezone.utc)

    # Compute the UTC offset
    offset = local_dt - utc_dt

    # Express the offset in hours:minutes
    offset_minutes = int(offset.total_seconds() / 60)
    offset_hours = offset_minutes // 60
    offset_minutes %= 60
    if offset.total_seconds() == 0:
        return ""
    return f"{offset_hours:+03d}:{offset_minutes:02d}"


def format_period(period: str) -> str:
    if period == 'day':
        return 'Daily'
    else:
        return period.capitalize() + 'ly'


def slugify(value, allow_unicode=False):
    """
    Taken from https://github.com/django/django/blob/master/django/utils/text.py
    Convert to ASCII if 'allow_unicode' is False. Convert spaces or repeated
    dashes to single dashes. Remove characters that aren't alphanumerics,
    underscores, or hyphens. Convert to lowercase. Also strip leading and
    trailing whitespace, dashes, and underscores.
    """
    value = str(value)
    if allow_unicode:
        value = unicodedata.normalize('NFKC', value)
    else:
        value = unicodedata.normalize('NFKD', value).encode('ascii', 'ignore').decode('ascii')
    value = re.sub(r'[^\w\s-]', '', value.lower())
    return re.sub(r'[-\s]+', '-', value).strip('-_')


def alternate_parse_settings(path: str):
    def parse(value: str) -> Union[int, str, bool]:
        if value.startswith('"'):
            return value[1:-1]
        elif value == 'true':
            return True
        elif value == 'false':
            return False
        else:
            return int(value)

    exp1 = re.compile(r'cfg\["(?P<key>.*)"\] = (?P<value>.*)')
    exp2 = re.compile(r'cfg\["(?P<key1>.*)"\]\[(?P<key2>.*)\] = (?P<value>.*)')

    settings = dict()
    with open(path, encoding='utf-8') as infile:
        for idx, line in enumerate(infile.readlines()):
            if idx == 0:
                continue
            match = exp2.search(line)
            if match:
                if match.group('key2').isnumeric():
                    settings[match.group('key1')].insert(int(match.group('key2')) - 1, parse(match.group('value')))
                else:
                    settings[match.group('key1')][parse(match.group('key2'))] = parse(match.group('value'))
            else:
                match = exp1.search(line)
                if match:
                    if match.group('value') == "{}":
                        if match.group('key') == 'missionList':
                            settings['missionList'] = list()
                        else:
                            settings[match.group('key')] = dict()
                    else:
                        settings[match.group('key')] = parse(match.group('value'))
    return settings


def get_all_servers(self) -> list[str]:
    with self.pool.connection() as conn:
        return [
            row[0] for row in conn.execute(
                "SELECT server_name FROM instances WHERE last_seen > (DATE(NOW()) - interval '1 week')"
            ).fetchall()
        ]


def get_all_players(self, linked: Optional[bool] = None, watchlist: Optional[bool] = None,
                    vip: Optional[bool] = None) -> list[Tuple[str, str]]:
    sql = "SELECT ucid, name FROM players WHERE length(ucid) = 32"
    if watchlist:
        sql += " AND watchlist IS NOT FALSE"
    if vip:
        sql += " AND vip IS NOT FALSE"
    if linked is not None:
        if linked:
            sql += " AND discord_id != -1"
        else:
            sql += " AND discord_id = -1"
    with self.pool.connection() as conn:
        return [(row[0], row[1]) for row in conn.execute(sql).fetchall()]


def is_ucid(ucid: Optional[str]) -> bool:
    if not ucid:
        return False
    return len(ucid) == 32 and ucid.isalnum() and ucid == ucid.lower()


def get_presets() -> Iterable[str]:
    presets = set()
    for file in Path('config').glob('presets*.yaml'):
        with open(file, encoding='utf-8') as infile:
            presets |= set([
                name for name, value in yaml.load(infile).items()
                if isinstance(value, dict) and not value.get('hidden', False)
            ])
    return presets


def get_preset(name: str, filename: Optional[str] = None) -> Optional[dict]:
    def _get_preset_from_file(filename: str) -> Optional[dict]:
        with open(filename, encoding='utf-8') as infile:
            data = yaml.load(infile)
            if name in data:
                return data[name]
        return None

    if filename:
        return _get_preset_from_file(filename)
    else:
        for file in Path('config').glob('presets*.yaml'):
            preset = _get_preset_from_file(str(file))
            if preset:
                return preset
    return None


def is_valid_url(url: str) -> bool:
    try:
        result = urlparse(url)
        return all([result.scheme, result.netloc])
    except ValueError:
        return False


def is_github_repo(url: str) -> bool:
    return is_valid_url(url) and url.startswith('https://github.com/') and not url.endswith('.zip')


def matches_cron(datetime_obj: datetime, cron_string: str):
    cron_job = croniter(cron_string, datetime_obj)
    next_date = cron_job.get_next(datetime)
    prev_date = cron_job.get_prev(datetime)
    return datetime_obj == prev_date or datetime_obj == next_date


class SettingsDict(dict):
    def __init__(self, obj: DataObject, path: str, root: str):
        super().__init__()
        self.path = path
        self.root = root
        self.mtime = 0
        self.obj = obj
        self.log = obj.log
        self.read_file()

    def read_file(self):
        self.mtime = os.path.getmtime(self.path)
        if self.path.lower().endswith('.lua'):
            try:
                data = luadata.read(self.path, encoding='utf-8')
            except Exception as ex:
                self.log.debug(f"Exception while reading {self.path}:\n{ex}")
                data = alternate_parse_settings(self.path)
                if not data:
                    self.log.error("- Error while parsing {}!".format(os.path.basename(self.path)))
                    raise ex
        elif self.path.lower().endswith('.yaml'):
            with open(self.path, encoding='utf-8') as file:
                data = yaml.load(file)
        if data:
            self.clear()
            self.update(data)

    def write_file(self):
        # DO NOT write empty config files. This means in general that there is an error.
        if not len(self):
            return
        tmpfd, tmpname = tempfile.mkstemp()
        os.close(tmpfd)
        if self.path.lower().endswith('.lua'):
            with open(tmpname, 'wb') as outfile:
                outfile.write((f"{self.root} = " + luadata.serialize(self, indent='\t',
                                                                     indent_level=0)).encode('utf-8'))
        elif self.path.lower().endswith('.json'):
            with open(tmpname, "w", encoding='utf-8') as outfile:
                yaml.dump(self, outfile)
        shutil.copy2(tmpname, self.path)
        self.mtime = os.path.getmtime(self.path)

    def __setitem__(self, key, value):
        if self.mtime < os.path.getmtime(self.path):
            self.log.debug(f'{self.path} changed, re-reading from disk.')
            self.read_file()
        super().__setitem__(key, value)
        if len(self):
            self.write_file()
        else:
            self.log.error("- Writing of {} aborted due to empty set.".format(os.path.basename(self.path)))

    def __getitem__(self, item):
        if self.mtime < os.path.getmtime(self.path):
            self.log.debug(f'{self.path} changed, re-reading from disk.')
            self.read_file()
        return super().__getitem__(item)


class RemoteSettingsDict(dict):
    def __init__(self, server: ServerProxy, obj: str, data: Optional[dict] = None):
        self.server = server
        self.obj = obj
        if data:
            super().__init__(data)

    def __setitem__(self, key, value):
        super().__setitem__(key, value)
        msg = {
            "command": "rpc",
            "object": "Server",
            "method": "_settings.__setitem__",
            "params": {
                "key": key,
                "value": value
            }
        }
        self.server.send_to_dcs(msg)


def evaluate(value: Union[str, int, float, bool], **kwargs) -> Union[str, int, float, bool]:
    if isinstance(value, (int, float, bool)) or not value.startswith('$'):
        return value
    return eval(format_string(value[1:], **kwargs))


def for_each(data: dict, search: list[str], depth: Optional[int] = 0, *,
             debug: Optional[bool] = False, **kwargs) -> Generator[dict]:
    def process_iteration(_next, data, search, depth, debug):
        if isinstance(data, list):
            for value in data:
                yield from for_each(value, search, depth + 1, debug=debug)
        elif isinstance(data, dict):
            for value in data.values():
                yield from for_each(value, search, depth + 1, debug=debug)

    def process_indexing(_next, data, search, depth, debug):
        if isinstance(data, list):
            indexes = [int(x.strip()) for x in _next[1:-1].split(',')]
            for index in indexes:
                if index <= 0 or len(data) < index:
                    if debug:
                        print("  " * depth + f"|_ {index}. element not found")
                    yield None
                if debug:
                    print("  " * depth + f"|_ Selecting {index}. element")
                yield from for_each(data[index - 1], search, depth + 1, debug=debug)
        elif isinstance(data, dict):
            indexes = [x.strip() for x in _next[1:-1].split(',')]
            for index in indexes:
                if index not in data:
                    if debug:
                        print("  " * depth + f"|_ {index}. element not found")
                    yield None
                if debug:
                    print("  " * depth + f"|_ Selecting element {index}")
                yield from for_each(data[index], search, depth + 1, debug=debug)

    def process_pattern(_next, data, search, depth, debug, **kwargs):
        if isinstance(data, list):
            for idx, value in enumerate(data):
                if evaluate(_next, **(kwargs | value)):
                    if debug:
                        print("  " * depth + f"  - Element {idx + 1} matches.")
                    yield from for_each(value, search, depth + 1, debug=debug)
        else:
            if evaluate(_next, **(kwargs | data)):
                if debug:
                    print("  " * depth + "  - Element matches.")
                yield from for_each(data, search, depth + 1, debug=debug)

    if not data or len(search) == depth:
        if debug:
            print("  " * depth + ("|_ RESULT found => Processing ..." if data else "|_ NO result found, skipping."))
        yield data
    else:
        _next = search[depth]
        if _next == '*':
            if debug:
                print("  " * depth + f"|_ Iterating over {len(data)} {search[depth - 1]} elements")
            yield from process_iteration(_next, data, search, depth, debug)
        elif _next.startswith('['):
            yield from process_indexing(_next, data, search, depth, debug)
        elif _next.startswith('$'):
            if debug:
                print("  " * depth + f"|_ Searching pattern {_next} on {len(data)} {search[depth - 1]} elements")
            yield from process_pattern(_next, data, search, depth, debug, **kwargs)
        elif _next in data:
            if debug:
                print("  " * depth + f"|_ {_next} found.")
            yield from for_each(data.get(_next), search, depth + 1, debug=debug)
        else:
            if debug:
                print("  " * depth + f"|_ {_next} not found.")
            yield None


class YAMLError(Exception):
    def __init__(self, file: str, ex: Union[ParserError, ScannerError]):
        super().__init__(f"Error in {file}, " + ex.__str__().replace('"<unicode string>"', file))
