-- FUNCTION: public.greenie_board_data(text, text, text, integer, integer)

-- DROP FUNCTION IF EXISTS public.greenie_board_data(text, text, text, integer, integer);

CREATE OR REPLACE FUNCTION public.greenie_board_data(
	server_name text,
	aircraft_type text,
	players_name text DEFAULT '%'::text,
	min_flight_time integer DEFAULT 0,
	return_limit integer DEFAULT 1000)
    RETURNS TABLE(player_name text, server_name text, flightlog_id text, aircraft_type text, case_num integer, points numeric, server_timestamp timestamp without time zone) 
    LANGUAGE 'sql'
    COST 100
    VOLATILE PARALLEL UNSAFE
    ROWS 1000

AS $BODY$
							SELECT
                         	DISTINCT ON (vnao_flightlogs.id)
								players.name,
                                missions.server_name AS server_name,
								vnao_flightlogs.id AS flightlog_id,
								-- vnao_lsogrades.id AS lsograde_id,
                                vnao_lsogrades.aircraft_type,
                                vnao_lsogrades.case_num,
                                -- vnao_flightlogs.flight_time as flight_time,
                                round(vnao_lsogrades.points, 1),
                                min(vnao_lsogrades.server_timestamp)
                            FROM
                                vnao_lsogrades
                                LEFT OUTER JOIN players
                                ON players.ucid = vnao_lsogrades.player_ucid
                                LEFT OUTER JOIN missions
                                ON vnao_lsogrades.mission_id = missions.id
								LEFT OUTER JOIN vnao_flightlogs
								ON vnao_lsogrades.flightlog_id = vnao_flightlogs.id
                            WHERE
                                missions.server_name = $1
                                AND vnao_lsogrades.aircraft_type = $2
                                AND players.name like $3
                            	AND vnao_flightlogs.flight_time > $4
                                AND extract(YEAR FROM vnao_lsogrades.server_timestamp) = extract(YEAR FROM now())
                                AND extract(MONTH FROM vnao_lsogrades.server_timestamp) = extract(MONTH FROM now())
                                AND vnao_lsogrades.grade <> 'WOFD'
					
							GROUP BY
								vnao_flightlogs.id,
								-- vnao_lsogrades.id,
								players.name,
								missions.server_name,
                                vnao_lsogrades.aircraft_type,
                                vnao_lsogrades.case_num,
								vnao_lsogrades.points
                            LIMIT
                               $5
$BODY$;

ALTER FUNCTION public.greenie_board_data(text, text, text, integer, integer)
    OWNER TO dcsserverbot;
