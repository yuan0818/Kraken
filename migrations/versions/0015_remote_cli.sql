ALTER TABLE remote_actions ADD COLUMN started_at timestamptz, ADD COLUMN finished_at timestamptz;
CREATE INDEX remote_cli_history ON remote_actions(device_id,created_at DESC,id DESC)
 WHERE payload->>'kind'='shell';
