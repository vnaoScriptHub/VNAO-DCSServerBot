import asyncio
import os
import psycopg

from contextlib import closing
from copy import deepcopy
from core import (
    EventListener,
    Server,
    Plugin,
    Player,
    event,
    chat_command,
    PersistentReport,
)
from datetime import datetime
from pathlib import Path
from pprint import pprint
from psycopg.rows import dict_row
from services import DCSServerBot
from plugins.creditsystem.player import CreditPlayer


class VnaoEventListener(EventListener):
    def __init__(self, plugin: Plugin):
        super().__init__(plugin)

    async def shutdown(self) -> None:
        await super().shutdown()

    def _record_flightlog_new_db(
        self, config: dict, server: Server, player: Player, data: dict
    ):
        self.log.debug(f"Opening flight log for: {player.name}")
        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                cursor.execute(
                    "INSERT INTO vnao_flightlogs (id, player_ucid, mission_id, server_name, aircraft_type, departure_field, arrival_field, "
                    "coalition, touch_downs, dead, crashed, ejected, air_start, mission_end, departure_timestamp) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        data["flightlog"]["id"],
                        player.ucid,
                        server.mission_id,
                        server.name,
                        player.unit_type,
                        data["flightlog"]["departureField"],
                        data["flightlog"]["arrivalField"],
                        data["coalition"],
                        data["flightlog"]["touchDowns"],
                        data["flightlog"]["dead"],
                        data["flightlog"]["crash"],
                        data["flightlog"]["ejected"],
                        data["flightlog"]["airStart"],
                        data["flightlog"]["missionEnd"],
                        data["flightlog"]["deptTime"],
                    ),
                )
                self.log.debug(f"Opened flightlog for: {player.name}")
            conn.commit()

    def _record_flightlog_update_db(
        self, config: dict, server: Server, player: Player, data: dict
    ):
        self.log.debug(f"Updating flightlog for: {player.name}")
        current_time = datetime.now()
        departure_time = datetime.strptime(
            data["flightlog"]["deptTime"], "%m/%d/%y %H:%M:%S"
        )
        total_flight_time = current_time - departure_time

        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                cursor.execute(
                    "UPDATE vnao_flightlogs "
                    "SET flight_time = %s, arrival_field = %s, touch_downs = %s, dead = %s, crashed = %s, ejected = %s, air_start = %s, mission_end = %s "
                    "WHERE id = %s",
                    (
                        int(total_flight_time.total_seconds()),
                        data["flightlog"]["arrivalField"],
                        data["flightlog"]["touchDowns"],
                        data["flightlog"]["dead"],
                        data["flightlog"]["crash"],
                        data["flightlog"]["ejected"],
                        data["flightlog"]["airStart"],
                        data["flightlog"]["missionEnd"],
                        data["flightlog"]["id"],
                    ),
                )
                self.log.debug(f"Flightlog updated for: {player.name}")
            conn.commit()

    def _record_flightlog_close_db(
        self, config: dict, server: Server, player: Player, data: dict
    ):
        self.log.debug(f"Closing flight log for: {player.name}")
        current_time = datetime.now()
        departure_time = datetime.strptime(
            data["flightlog"]["deptTime"], "%m/%d/%y %H:%M:%S"
        )
        total_flight_time = current_time - departure_time

        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                cursor.execute(
                    "UPDATE vnao_flightlogs "
                    "SET flight_time = %s, arrival_field = %s, touch_downs = %s, dead = %s, crashed = %s, ejected = %s, air_start = %s, mission_end = %s, arrival_timestamp = %s "
                    "WHERE id = %s",
                    (
                        int(total_flight_time.total_seconds()),
                        data["flightlog"]["arrivalField"],
                        data["flightlog"]["touchDowns"],
                        data["flightlog"]["dead"],
                        data["flightlog"]["crash"],
                        data["flightlog"]["ejected"],
                        data["flightlog"]["airStart"],
                        data["flightlog"]["missionEnd"],
                        datetime.now(),
                        data["flightlog"]["id"],
                    ),
                )
                self.log.debug(f"Flightlog closed for: {player.name}")
            conn.commit()

    @event(name="onFlightLogNew")
    async def onFlightLogNew(self, server: Server, data: dict) -> None:
        self.log.debug(f"onFlightLogNew event received.")
        config = self.get_config(server)
        player: Player = (
            server.get_player(name=data["callsign"]) if "callsign" in data else None
        )
        if player:
            self._record_flightlog_new_db(config, server, player, data)

    @event(name="onFlightLogaUpdate")
    async def onFlightLogaUpdate(self, server: Server, data: dict) -> None:
        self.log.debug(f"onFlightLogUpdate event received.")
        config = self.get_config(server)
        player: Player = (
            server.get_player(name=data["callsign"]) if "callsign" in data else None
        )
        if player:
            self._record_flightlog_update_db(config, server, player, data)

    @event(name="onFlightLogClose")
    async def onFlightLogClose(self, server: Server, data: dict) -> None:
        self.log.debug(f"onFlightLogClose event received.")
        config = self.get_config(server)
        player: Player = (
            server.get_player(name=data["callsign"]) if "callsign" in data else None
        )
        if player:
            self._record_flightlog_close_db(config, server, player, data)


    def _squadron_check(self, data: dict, config: dict) -> dict:
        self.log.debug("Checking if this is a squadron flight.")
        is_squadron_flight: bool = False
        squadron_tag: str = None
        squadron_name: str = None
        squadron_aircraft: str = None
        flight_time: int = None

        # First check to see if the pilot's name contains a squadron tag
        for key, val in config["greenie_boards"]["squadron_tags"].items():
            if key in data["name"]:
                squadron_tag = key
                squadron_name = val["display_name"]
                squadron_aircraft = val["aircraft"]
                break

        # If a squdron tag was found and the squadron and flight aircraft match, check to see if the flight time was long enough.
        # If a squadron tag was not found and the squadron and flight aircraft do not match, we can stop now.
        # If a squadron tag was found, the squadron and flight aircraft match and the flight time is long enough, we have a
        # squadron trap.
        if squadron_tag and squadron_aircraft == data["airframe"]:
            # Get the flightlog flight time that this trap belongs to, we have to go out to the database
            # and check the vnao_flightlogs table as this is where the flight time is recorded
            with self.pool.connection() as conn:
                with closing(conn.cursor(row_factory=dict_row)) as cursor:
                    # grab the departure timestamp and see if we are long enough for a squadron flight
                    cursor.execute(
                        "SELECT departure_timestamp FROM vnao_flightlogs WHERE id = %s",
                        (data["flightlogID"],),
                    )
                    departure_time = cursor.fetchone()['departure_timestamp']
                    current_time = datetime.now()
                    flight_time = current_time - departure_time
                    self.log.debug(f"Checking for squadron flight.  departure time: {departure_time}\tflight time: {flight_time}")

                    if int(flight_time.total_seconds()) >= int(
                        config["greenie_boards"]["squadron_time_min"]
                    ):
                        is_squadron_flight = True

        return {
            "squadron_tag": squadron_tag,
            "squadron_name": squadron_name,
            "is_squadron_flight": is_squadron_flight,
        }

    async def _update_greenieboards(self, server: Server, config: dict, data: dict):
        self.log.debug("Updating Greenie board.")
        squadron_check_result = deepcopy(self._squadron_check(data, config))
        self.log.debug(f"Squadron data: {squadron_check_result}")

        # If it's a squadron flight, update squadron baord
        if squadron_check_result["is_squadron_flight"] == True:
            self.log.debug("Greenie board type: Squadron")

            if "persistent_squadron_channel" in config and config.get(
                "persistent_squadron_channel", True
            ):
                channel_id = int(config["persistent_squadron_channel"])

                report = PersistentReport(
                    self.bot,
                    self.plugin_name,
                    "greenieboard.json",
                    server=server,
                    embed_name=f"{server.name}-squadron-{squadron_check_result['squadron_tag']}",
                    channel_id=channel_id,
                )
                await report.render(
                    server_name=server.name,
                    config=config,
                    trap_data=data,
                    squadron_data=deepcopy(squadron_check_result),
                )
            else:
                self.log.debug("Missing persistent_squadron_channel from vnao.yaml.")

            # Reset the is_squadron_flight flag to false so we can update the practice board as well.
            squadron_check_result["is_squadron_flight"] = False

        # Update the practice boards always!
        self.log.debug("Greenie board type: Practice")
        if "persistent_practice_channel" in config and config.get(
            "persistent_practice_channel", True
        ):
            channel_id = int(config["persistent_practice_channel"])

            report = PersistentReport(
                self.bot,
                self.plugin_name,
                "greenieboard.json",
                server=server,
                embed_name=f"{server.name}-practice-{data['airframe']}",
                channel_id=channel_id,
            )
            await report.render(
                server_name=server.name,
                config=config,
                trap_data=data,
                squadron_data=deepcopy(squadron_check_result),
            )
        else:
            self.log.debug("Missing persistent_practice_channel from vnao.yaml.")

        self.log.debug("Completed updating greenieboards.")

    def _record_lso_grade_db(
        self, config: dict, server: Server, player: Player, data: dict
    ):
        self.log.debug(f"Processing LSO grade event")
        time_string = f"{data['midate']} {data['mitime']}"
        mission_timestamp = datetime.strptime(time_string, "%Y/%m/%d %H:%M:%S")

        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                cursor.execute(
                    "INSERT INTO vnao_lsogrades (flightlog_id, player_ucid, mission_id, server_name, aircraft_type, points, "
                    "grade, details, wire, case_num, wind, time_groove, carrier_name, carrier_type, trapsheet, mission_timestamp) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        data["flightlogID"],
                        player.ucid,
                        server.mission_id,
                        server.name,
                        player.unit_type,
                        data.get("points", 0),
                        data.get("grade", 0),
                        data["details"],
                        data.get("wire", 0),
                        data["case"],
                        data["wind"],
                        data.get("Tgroove", 0),
                        data["carriername"],
                        data["carriertype"],
                        '{"n/a": "n/a"}',  # trapsheet_data,
                        mission_timestamp,
                    ),
                )
            conn.commit()

    @event(name="moose_lso_grade")
    async def moose_lso_grade(self, server: Server, data: dict):
        self.log.debug("Recieved lso grade from Moose.")
        config = self.get_config(server)

        player: Player = (
            server.get_player(name=data["name"]) if "name" in data else None
        )

        self.log.debug(f"Player: {player.name}")

        if player:
            self.log.debug("Calling _record_lso_grade_db.")
            self._record_lso_grade_db(config, server, player, data)

            # Flight logs wait 15 seconds before submitting to the database incase the landing is a touch and go
            # So we wait another 5 seconds (20 total) to make sure the flight log has been closed and the total
            # flight time has been added to the database.
            # Then we can proceed with building the greenieboards.
            # This needs to done a better way to ensure that the flight log has been closed for sure, currently
            # with this approach, we are 'hoping' the flight log has been closed by the time we pause and make the
            # call.
            # await asyncio.sleep(30)

            self.log.debug("Calling _update_greenieboards.")
            await self._update_greenieboards(server, config, data)

    async def _update_rangeboards(self, server: Server, board_type: str):
        # shall we render the server specific board?
        self.log.debug(f"Updating range board.")
        config = self.plugin.get_config(server)
        channel_id = int(config["persistent_range_channel"])

        if board_type == "bomb":
            self.log.debug("Range board type: Bomb")
            report = PersistentReport(
                self.bot,
                self.plugin_name,
                "rangeboard.json",
                server=server,
                embed_name=f"bombboard-{server.name}",
                channel_id=channel_id,
            )
            await report.render(
                server_name=server.name, config=config, board_type=board_type
            )
        else:
            self.log.debug("Range board type: Strafe")
            report = PersistentReport(
                self.bot,
                self.plugin_name,
                "rangeboard.json",
                server=server,
                embed_name=f"strafeboard-{server.name}",
                channel_id=channel_id,
            )
            await report.render(
                server_name=server.server_name, config=config, board_type=board_type
            )

    def _record_bomb_db(self, config: dict, server: Server, player: Player, data: dict):
        self.log.debug(f"Processing bomb event.")

        points = config["range_boards"]["ratings_bomb"][data["quality"]] or 0

        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                cursor.execute(
                    "INSERT INTO vnao_bombboard (mission_id, player_ucid, unit_type, points, quality, weapon, theatre, range_name, target_name, distance, "
                    "radial, heading, velocity, altitude, bombsheet, clock, midate) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        server.mission_id,
                        player.ucid,
                        player.unit_type,
                        points,
                        data["quality"],
                        data["weapon"],
                        data["theatre"],
                        data["rangename"],
                        data["name"],
                        data["distance"],
                        data["radial"],
                        data["attackHdg"],
                        data["attackVel"],
                        data["attackAlt"],
                        data["bombsheet"] if "bombsheet" in data else None,
                        data["clock"],
                        data["midate"],
                    ),
                )
                self.log.debug("Bomb pass added to database.")
            conn.commit()

    def _record_strafe_db(
        self, config: dict, server: Server, player: Player, data: dict
    ):
        self.log.debug(f"Processing strafe event")

        points = config["range_boards"]["ratings_strafe"][data["roundsQuality"]] or 0

        with self.pool.connection() as conn:
            with closing(conn.cursor(row_factory=dict_row)) as cursor:
                cursor.execute(
                    "INSERT INTO vnao_strafeboard (mission_id, player_ucid, unit_type, points, quality, theatre, range_name, target_name, "
                    "rounds_fired, rounds_hit, accuracy, strafesheet, clock, midate) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        server.mission_id,
                        player.ucid,
                        player.unit_type,
                        points,
                        data["roundsQuality"],
                        data["theatre"],
                        data["rangename"],
                        data["name"],
                        data["roundsFired"],
                        data["roundsHit"],
                        data["strafeAccuracy"],
                        data["strafesheet"] if "strafesheet" in data else None,
                        data["clock"],
                        data["midate"],
                    ),
                )
                self.log.debug("Strafe pass added to database.")
            conn.commit()

    @event(name="moose_bomb_result")
    async def moose_bomb_result(self, server: Server, data: dict):
        self.log.debug(f"Received bomb result from Moose - {data}")
        config = self.plugin.get_config(server)
        player: Player = server.get_player(name=data['player'])

        if player:
            self.log.debug(f"Player {player.name}")
            self._record_bomb_db(config, server, player, data)
            await self._update_rangeboards(server, "bomb")
        else:
            self.log.debug(f"Player not found: {data['player']}")

    @event(name="moose_strafe_result")
    async def moose_strafe_result(self, server: Server, data: dict):
        self.log.debug(f"Received strafe result from Moose - {data}")      
        config = self.plugin.get_config(server)
        player: Player = server.get_player(name=data['player'])
        
        if player:
            self.log.debug(f"Player {player.name}")
            self._record_strafe_db(config, server, player, data)
            await self._update_rangeboards(server, "strafe")
        else:
            self.log.debug(f"Player not found: {data['player']}")