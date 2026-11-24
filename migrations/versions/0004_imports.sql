CREATE TABLE import_uploads (
 id uuid PRIMARY KEY, filename text NOT NULL, sha256 text NOT NULL,
 original bytea NOT NULL, workbook jsonb NOT NULL,
 created_by text NOT NULL REFERENCES subjects(id), created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE import_batches ADD COLUMN upload_id uuid REFERENCES import_uploads(id);
ALTER TABLE import_batches ADD COLUMN request_id uuid UNIQUE;
ALTER TABLE import_batches ADD COLUMN fingerprint text;
ALTER TABLE import_batches ADD COLUMN configuration jsonb NOT NULL DEFAULT '{}';
ALTER TABLE import_batches ADD COLUMN preview jsonb NOT NULL DEFAULT '[]';
ALTER TABLE import_batches ADD COLUMN acceptance jsonb;
ALTER TABLE import_batches ADD COLUMN result jsonb;
ALTER TABLE import_batches ADD COLUMN created_by text REFERENCES subjects(id);
CREATE TABLE case_source_links (
 source text NOT NULL CHECK(source IN ('excel','feishu')), source_key text NOT NULL,
 sheet text NOT NULL, source_code text NOT NULL, case_id uuid NOT NULL REFERENCES cases(id),
 version_id uuid NOT NULL REFERENCES case_versions(id), source_digest text NOT NULL,
 batch_id uuid NOT NULL REFERENCES import_batches(id),
 PRIMARY KEY(source,source_key,sheet,source_code)
);
CREATE INDEX import_batches_created ON import_batches(created_at DESC);
-- Version order follows the insert after acquiring the case lock, not transaction start.
ALTER TABLE case_versions ALTER COLUMN created_at SET DEFAULT clock_timestamp();
