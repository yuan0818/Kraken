CREATE TABLE subjects (id text PRIMARY KEY, authentication text NOT NULL, role text NOT NULL);
INSERT INTO subjects VALUES ('anonymous', 'open', 'admin');
CREATE TABLE cases (
 id uuid PRIMARY KEY, name text NOT NULL, stable_version_id uuid,
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE case_versions (
 id uuid PRIMARY KEY, case_id uuid NOT NULL REFERENCES cases(id),
 content jsonb NOT NULL, created_by text NOT NULL REFERENCES subjects(id),
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(case_id,id)
);
ALTER TABLE cases ADD CONSTRAINT stable_same_case FOREIGN KEY (id,stable_version_id)
 REFERENCES case_versions(case_id,id);
CREATE FUNCTION immutable_version() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'CaseVersion is immutable'; END $$;
CREATE TRIGGER immutable_version BEFORE UPDATE OR DELETE ON case_versions
 FOR EACH ROW EXECUTE FUNCTION immutable_version();
CREATE TABLE schedules (
 id uuid PRIMARY KEY, name text NOT NULL, timezone text NOT NULL, expression text NOT NULL,
 enabled boolean NOT NULL DEFAULT false, checkpoint_at timestamptz,
 definition jsonb NOT NULL DEFAULT '{}'
);
CREATE TABLE runs (
 id uuid PRIMARY KEY, request_id uuid UNIQUE NOT NULL, request_fingerprint text NOT NULL,
 artifacts jsonb NOT NULL DEFAULT '{}', schedule_id uuid REFERENCES schedules(id),
 scheduled_at timestamptz, created_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(schedule_id,scheduled_at)
);
CREATE TABLE case_runs (
 id uuid PRIMARY KEY, run_id uuid NOT NULL REFERENCES runs(id),
 case_id uuid NOT NULL REFERENCES cases(id), UNIQUE(run_id,case_id)
);
CREATE TABLE nodes (
 id text PRIMARY KEY, token_hash text NOT NULL, capabilities jsonb NOT NULL,
 connection_generation bigint NOT NULL DEFAULT 0, last_event_seq bigint NOT NULL DEFAULT 0,
 last_seen timestamptz
);
CREATE TABLE devices (
 id uuid PRIMARY KEY, node_id text NOT NULL REFERENCES nodes(id),
 serial text NOT NULL, adb_endpoint text NOT NULL DEFAULT 'local', tags jsonb NOT NULL,
 generation bigint NOT NULL DEFAULT 0, quarantined boolean NOT NULL DEFAULT false,
 UNIQUE(node_id,adb_endpoint,serial)
);
CREATE TABLE attempts (
 id uuid PRIMARY KEY, case_run_id uuid NOT NULL REFERENCES case_runs(id), number int NOT NULL CHECK(number>0),
 state text NOT NULL DEFAULT 'queued' CHECK(state IN
 ('queued','preparing','running','cancel_pending','completed','cancelled','blocked','needs_recovery')),
 version_id uuid REFERENCES case_versions(id), snapshot jsonb,
 verdict text NOT NULL DEFAULT 'not_evaluated' CHECK(verdict IN ('not_evaluated','pass','fail','blocked','inconclusive')),
 cleanup text NOT NULL DEFAULT 'pending' CHECK(cleanup IN ('pending','verified','failed')),
 evidence text NOT NULL DEFAULT 'missing' CHECK(evidence IN ('missing','partial','complete')),
 reason text NOT NULL DEFAULT '', simulation boolean NOT NULL DEFAULT true CHECK(simulation),
 created_at timestamptz NOT NULL DEFAULT now(), UNIQUE(case_run_id,number),
 CHECK((version_id IS NULL) = (snapshot IS NULL)),
 CHECK(verdict != 'pass' OR (evidence='complete' AND cleanup='verified' AND NOT simulation))
);
CREATE FUNCTION immutable_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.snapshot IS NOT NULL AND (NEW.snapshot IS DISTINCT FROM OLD.snapshot OR NEW.version_id IS DISTINCT FROM OLD.version_id)
 THEN RAISE EXCEPTION 'Attempt snapshot is immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER immutable_snapshot BEFORE UPDATE ON attempts FOR EACH ROW EXECUTE FUNCTION immutable_snapshot();
CREATE TABLE leases (
 id uuid PRIMARY KEY, device_id uuid NOT NULL REFERENCES devices(id),
 attempt_id uuid NOT NULL REFERENCES attempts(id), generation bigint NOT NULL CHECK(generation>0),
 purpose text NOT NULL DEFAULT 'test' CHECK(purpose IN ('test','flash','remote','maintenance')),
 acquired_at timestamptz NOT NULL DEFAULT now(), released_at timestamptz,
 UNIQUE(device_id,generation)
);
CREATE UNIQUE INDEX one_active_device_lease ON leases(device_id) WHERE released_at IS NULL;
CREATE UNIQUE INDEX one_active_attempt_lease ON leases(attempt_id) WHERE released_at IS NULL;
CREATE TABLE commands (
 id uuid PRIMARY KEY, attempt_id uuid NOT NULL REFERENCES attempts(id),
 node_id text NOT NULL REFERENCES nodes(id), payload jsonb NOT NULL,
 dispatch_ready boolean NOT NULL DEFAULT false, accepted boolean NOT NULL DEFAULT false,
 retired boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX command_kind_per_attempt ON commands(attempt_id, (payload->>'kind'));
CREATE TABLE outbox (
 id uuid PRIMARY KEY, command_id uuid UNIQUE NOT NULL REFERENCES commands(id),
 published_at timestamptz, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE node_events (
 node_id text NOT NULL REFERENCES nodes(id), seq bigint NOT NULL,
 payload jsonb NOT NULL, received_at timestamptz NOT NULL DEFAULT now(), PRIMARY KEY(node_id,seq)
);
CREATE TABLE import_batches (
 id uuid PRIMARY KEY, source text NOT NULL CHECK(source IN ('excel','feishu')),
 state text NOT NULL, source_identity jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE evidence (
 id uuid PRIMARY KEY, attempt_id uuid NOT NULL REFERENCES attempts(id),
 manifest jsonb NOT NULL, archive_verified_at timestamptz, retain_until timestamptz
);
CREATE TABLE audit (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, subject_id text NOT NULL REFERENCES subjects(id),
 action text NOT NULL, resource_id text NOT NULL, detail jsonb NOT NULL DEFAULT '{}',
 created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX queued_attempts ON attempts(created_at) WHERE state='queued';
CREATE INDEX pending_outbox ON outbox(created_at) WHERE published_at IS NULL;
CREATE INDEX node_pending_commands ON commands(node_id) WHERE NOT retired;
