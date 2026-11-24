ALTER TABLE devices ADD COLUMN artemis_state text CHECK (artemis_state IN ('ready','busy','unavailable'));
ALTER TABLE attempts DROP CONSTRAINT attempts_simulation_check;
