-- M2 imports: what each import read and refused, and the sha256 of the source line behind each imported row
-- (a row changed at its source is imported again as a correction, and the files can be checked row by row).
ALTER TABLE ops_ingest_runs ADD COLUMN rows_read INTEGER CHECK (rows_read IS NULL OR rows_read >= 0);
ALTER TABLE ops_ingest_runs ADD COLUMN rows_refused INTEGER CHECK (rows_refused IS NULL OR rows_refused >= 0);
ALTER TABLE ops_agent_runs ADD COLUMN source_line_sha256 TEXT
  CHECK (source_line_sha256 IS NULL OR length(source_line_sha256) = 64);
ALTER TABLE research_trials ADD COLUMN source_line_sha256 TEXT
  CHECK (source_line_sha256 IS NULL OR length(source_line_sha256) = 64);
