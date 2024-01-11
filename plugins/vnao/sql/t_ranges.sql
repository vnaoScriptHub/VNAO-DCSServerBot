CREATE TABLE IF NOT EXISTS vnao_bombboard (id SERIAL PRIMARY KEY, mission_id INTEGER NOT NULL, player_ucid TEXT NOT NULL, unit_type TEXT NOT NULL, points INTEGER NOT NULL, quality TEXT NOT NULL, weapon TEXT NOT NULL, theatre TEXT NOT NULL, range_name TEXT NOT NULL, target_name TEXT NOT NULL, distance DECIMAL, radial DECIMAL, heading DECIMAL, velocity DECIMAL, altitude DECIMAL, bombsheet TEXT, clock TEXT, midate TIMESTAMP, time TIMESTAMP NOT NULL DEFAULT NOW());
ALTER TABLE IF EXISTS vnao_bomboard OWNER to dcsserverbot;
CREATE INDEX IF NOT EXISTS idx_bombboard_ucid ON vnao_bombboard(player_ucid);
CREATE TABLE IF NOT EXISTS vnao_strafeboard (id SERIAL PRIMARY KEY, mission_id INTEGER NOT NULL, player_ucid TEXT NOT NULL, unit_type TEXT NOT NULL, points INTEGER NOT NULL, quality TEXT NOT NULL, theatre TEXT NOT NULL, range_name TEXT NOT NULL, target_name TEXT NOT NULL, rounds_fired INTEGER, rounds_hit INTEGER, accuracy DECIMAL, strafesheet TEXT, clock TEXT, midate TIMESTAMP, time TIMESTAMP NOT NULL DEFAULT NOW());
ALTER TABLE IF EXISTS vnao_strafeboard OWNER to dcsserverbot;
CREATE INDEX IF NOT EXISTS idx_strafeboard_ucid ON vnao_strafeboard(player_ucid);
