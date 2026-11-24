-- Legacy hashes remain readable by older deployments during upgrades.
ALTER TABLE nodes ALTER COLUMN token_hash DROP NOT NULL;
