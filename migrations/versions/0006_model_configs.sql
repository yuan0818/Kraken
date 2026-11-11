CREATE TABLE model_credentials (
 id uuid PRIMARY KEY, ciphertext text NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE model_configs (
 id uuid PRIMARY KEY, revision int NOT NULL CHECK(revision > 0), enabled boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE model_config_versions (
 config_id uuid NOT NULL REFERENCES model_configs(id), revision int NOT NULL CHECK(revision > 0),
 definition jsonb NOT NULL, credential_id uuid REFERENCES model_credentials(id),
 created_by text NOT NULL REFERENCES subjects(id), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(config_id,revision)
);
ALTER TABLE model_configs ADD CONSTRAINT current_model_revision FOREIGN KEY(id,revision)
 REFERENCES model_config_versions(config_id,revision) DEFERRABLE INITIALLY DEFERRED;
CREATE TRIGGER immutable_model_revision BEFORE UPDATE OR DELETE ON model_config_versions
 FOR EACH ROW EXECUTE FUNCTION immutable_version();
CREATE TRIGGER immutable_model_credential BEFORE UPDATE OR DELETE ON model_credentials
 FOR EACH ROW EXECUTE FUNCTION immutable_version();
CREATE TABLE model_checks (
 id uuid PRIMARY KEY, config_id uuid NOT NULL, revision int NOT NULL,
 location text NOT NULL CHECK(location='control_plane'), status text NOT NULL CHECK(status IN ('passed','failed')),
 detail text NOT NULL, latency_ms int NOT NULL, http_status int,
 created_by text NOT NULL REFERENCES subjects(id), checked_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(config_id,revision) REFERENCES model_config_versions(config_id,revision)
);
CREATE INDEX model_check_history ON model_checks(config_id,checked_at DESC);
ALTER TABLE runs ADD COLUMN model_config_id uuid;
ALTER TABLE runs ADD COLUMN model_revision int;
ALTER TABLE runs ADD CONSTRAINT run_model_revision FOREIGN KEY(model_config_id,model_revision)
 REFERENCES model_config_versions(config_id,revision);
