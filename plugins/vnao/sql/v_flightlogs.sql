-- View: public.v_flightlogs

-- DROP VIEW public.v_flightlogs;

CREATE OR REPLACE VIEW public.v_flightlogs
 AS
 SELECT p.name,
    f.aircraft_type,
    f.flight_time,
    f.departure_field,
    f.arrival_field,
    f.touch_downs,
    f.dead,
    f.crashed,
    f.ejected,
    f.air_start,
    f.mission_end,
    f.arrival_timestamp
   FROM vnao_flightlogs f
     JOIN players p ON f.player_ucid = p.ucid
  ORDER BY f.arrival_timestamp DESC;

ALTER TABLE public.v_flightlogs
    OWNER TO dcsserverbot;

