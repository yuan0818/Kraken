ALTER TABLE devices
  ADD COLUMN adb_state text,
  ADD COLUMN adb_model text,
  ADD COLUMN adb_product text,
  ADD COLUMN observed_at timestamptz;
