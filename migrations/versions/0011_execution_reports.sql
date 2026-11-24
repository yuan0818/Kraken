CREATE TABLE execution_reports (
 attempt_id uuid PRIMARY KEY REFERENCES attempts(id) ON DELETE CASCADE,
 payload jsonb NOT NULL
);
