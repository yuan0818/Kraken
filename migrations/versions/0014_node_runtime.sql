ALTER TABLE nodes ADD COLUMN runtime_protocol integer NOT NULL DEFAULT 1;
ALTER TABLE commands ADD COLUMN launch_intent_at timestamptz;
-- Existing allocated execution may already have reached an old node. Never launch it again.
UPDATE commands c SET launch_intent_at=now() FROM attempts a
WHERE c.attempt_id=a.id AND c.payload->>'kind'='execute'
AND (c.accepted OR a.state IN ('starting','running','cancel_pending','needs_recovery'));
CREATE TABLE node_runtime_sessions (
 node_id text NOT NULL REFERENCES nodes(id),
 boot_id uuid NOT NULL,
 generation bigint NOT NULL,
 PRIMARY KEY(node_id,boot_id)
);
CREATE TABLE node_runtime_events (
 node_id text NOT NULL REFERENCES nodes(id),
 event_id uuid NOT NULL,
 payload jsonb NOT NULL,
 received_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(node_id,event_id)
);
