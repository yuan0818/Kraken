CREATE TABLE execution_frames (
 attempt_id uuid NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
 digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
 image bytea NOT NULL CHECK(octet_length(image) BETWEEN 1 AND 1048576),
 PRIMARY KEY (attempt_id, digest)
);
