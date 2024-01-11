-- View: public.v_lsogrades

-- DROP VIEW public.v_lsogrades;

CREATE OR REPLACE VIEW public.v_lsogrades
 AS
 SELECT p.name,
    l.aircraft_type,
    l.server_name,
    l.points,
    l.grade,
    l.details,
    l.wire,
    l.case_num,
    l.wind,
    l.time_groove,
    l.carrier_name,
    l.server_timestamp
   FROM vnao_lsogrades l
     JOIN players p ON l.player_ucid = p.ucid
  ORDER BY l.server_timestamp DESC;

ALTER TABLE public.v_lsogrades
    OWNER TO dcsserverbot;

