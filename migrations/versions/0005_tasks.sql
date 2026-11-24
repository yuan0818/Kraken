CREATE TABLE test_tasks (
 id uuid PRIMARY KEY, request_id uuid UNIQUE NOT NULL, name text NOT NULL, description text NOT NULL DEFAULT '',
 revision int NOT NULL DEFAULT 1, configuration jsonb NOT NULL, next_trigger_at timestamptz,
 created_by text NOT NULL REFERENCES subjects(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(), updated_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
INSERT INTO subjects(id,authentication,role) VALUES ('scheduler','system','admin') ON CONFLICT DO NOTHING;
CREATE TABLE task_cases (
 task_id uuid NOT NULL REFERENCES test_tasks(id), case_id uuid NOT NULL REFERENCES cases(id),
 position int NOT NULL, PRIMARY KEY(task_id,case_id), UNIQUE(task_id,position)
);
CREATE TABLE task_revisions (
 task_id uuid NOT NULL REFERENCES test_tasks(id), revision int NOT NULL, definition jsonb NOT NULL,
 created_by text NOT NULL REFERENCES subjects(id), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(task_id,revision)
);
CREATE TRIGGER immutable_task_revision BEFORE UPDATE OR DELETE ON task_revisions
 FOR EACH ROW EXECUTE FUNCTION immutable_version();
ALTER TABLE runs ADD COLUMN task_id uuid REFERENCES test_tasks(id);
ALTER TABLE runs ADD COLUMN task_revision int;
ALTER TABLE runs ADD COLUMN task_snapshot jsonb;
ALTER TABLE runs ADD COLUMN trigger text NOT NULL DEFAULT 'legacy';
ALTER TABLE runs ADD COLUMN trigger_at timestamptz;
ALTER TABLE runs ADD COLUMN blocked_reason text NOT NULL DEFAULT '';
ALTER TABLE runs ADD COLUMN cancel_requested boolean NOT NULL DEFAULT false;
ALTER TABLE runs ADD CONSTRAINT task_revision_reference FOREIGN KEY(task_id,task_revision) REFERENCES task_revisions(task_id,revision);
CREATE UNIQUE INDEX task_schedule_once ON runs(task_id,trigger_at) WHERE trigger='scheduled';
CREATE INDEX task_runs_created ON runs(task_id,created_at);
ALTER TABLE runs ALTER COLUMN created_at SET DEFAULT clock_timestamp();
ALTER TABLE case_runs ADD COLUMN position int NOT NULL DEFAULT 0;
ALTER TABLE attempts ADD COLUMN eligible_at timestamptz NOT NULL DEFAULT now();
ALTER TABLE attempts ADD COLUMN finished_at timestamptz;
CREATE FUNCTION mark_attempt_finished() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.state IN ('completed','cancelled','blocked') AND OLD.state IS DISTINCT FROM NEW.state THEN
  NEW.finished_at = clock_timestamp();
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER mark_attempt_finished BEFORE UPDATE ON attempts FOR EACH ROW EXECUTE FUNCTION mark_attempt_finished();
