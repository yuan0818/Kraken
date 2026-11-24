CREATE TABLE quality_media (
 attempt_id uuid NOT NULL REFERENCES attempts(id) ON DELETE CASCADE,
 digest text NOT NULL CHECK(digest ~ '^[a-f0-9]{64}$'),
 media_type text NOT NULL CHECK(media_type IN ('image/jpeg','video/mp4')),
 content bytea NOT NULL CHECK(octet_length(content) <= 33554432),
 PRIMARY KEY(attempt_id,digest)
);
