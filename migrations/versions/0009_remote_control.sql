ALTER TABLE devices ADD COLUMN remote_token text, ADD COLUMN remote_until timestamptz;
CREATE TABLE remote_actions (
 id uuid PRIMARY KEY, device_id uuid NOT NULL REFERENCES devices(id),
 token_hash text, payload jsonb NOT NULL,
 state text NOT NULL DEFAULT 'queued', result jsonb,
 created_at timestamptz NOT NULL DEFAULT now(), expires_at timestamptz NOT NULL DEFAULT now()+interval '15 seconds'
);
CREATE INDEX remote_pending ON remote_actions(device_id,created_at) WHERE state='queued';
