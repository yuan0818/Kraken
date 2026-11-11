CREATE SEQUENCE case_code_seq;
ALTER TABLE cases ADD COLUMN code text;
UPDATE cases SET code='TC-' || lpad(nextval('case_code_seq')::text,6,'0');
ALTER TABLE cases ALTER COLUMN code SET NOT NULL;
ALTER TABLE cases ALTER COLUMN code SET DEFAULT ('TC-' || lpad(nextval('case_code_seq')::text,6,'0'));
CREATE UNIQUE INDEX unique_case_code ON cases (lower(code));
CREATE TABLE catalog_entries (
 id uuid PRIMARY KEY,
 kind text NOT NULL CHECK(kind IN ('system_module','app_module','app_version','application','android_version','device_model','chip_platform')),
 name text NOT NULL CHECK(length(trim(name)) > 0),
 description text NOT NULL DEFAULT '',
 parent_id uuid REFERENCES catalog_entries(id),
 enabled boolean NOT NULL DEFAULT true,
 created_at timestamptz NOT NULL DEFAULT now(),
 updated_at timestamptz NOT NULL DEFAULT now(),
 CHECK((kind IN ('app_module','app_version')) = (parent_id IS NOT NULL))
);
CREATE UNIQUE INDEX unique_catalog_name ON catalog_entries(kind,coalesce(parent_id,'00000000-0000-0000-0000-000000000000'::uuid),lower(name));
CREATE TABLE case_version_catalog_refs (
 version_id uuid NOT NULL REFERENCES case_versions(id),
 entry_id uuid NOT NULL REFERENCES catalog_entries(id),
 PRIMARY KEY(version_id,entry_id)
);
CREATE INDEX case_version_latest ON case_versions(case_id,created_at DESC,id DESC);
CREATE INDEX catalog_references ON case_version_catalog_refs(entry_id);
