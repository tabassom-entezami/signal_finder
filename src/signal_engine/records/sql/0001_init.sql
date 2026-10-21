-- Research records store: 31 append-only tables (portable SQL; SQLite STRICT now, Postgres later).
-- Every table has the common columns id, created_utc, source, owner, supersedes. Natural keys are partial
-- unique indexes over the rows that correct nothing (`supersedes IS NULL`), so a correction can repeat its
-- key. Triggers and the one-successor index are added per table from triggers.sql.
-- Timestamps: YYYY-MM-DDTHH:MM:SS.ffffffZ (27 characters), checked without GLOB.

-- meta: catalog, sources, datasets, raw files, links no FK covers
CREATE TABLE meta_tables (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES meta_tables (id),
  name TEXT NOT NULL,
  group_name TEXT NOT NULL CHECK (group_name IN ('meta', 'ops', 'knowledge', 'decide', 'research', 'traders')),
  writer TEXT NOT NULL,
  truth TEXT NOT NULL,
  stage TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX meta_tables_key ON meta_tables (name) WHERE supersedes IS NULL;

CREATE TABLE meta_sources (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES meta_sources (id),
  url_or_path TEXT NOT NULL,
  terms_checked_utc TEXT CHECK (terms_checked_utc IS NULL OR (length(terms_checked_utc) = 27 AND substr(terms_checked_utc, 11, 1) = 'T' AND substr(terms_checked_utc, 27, 1) = 'Z')),
  robots_checked_utc TEXT CHECK (robots_checked_utc IS NULL OR (length(robots_checked_utc) = 27 AND substr(robots_checked_utc, 11, 1) = 'T' AND substr(robots_checked_utc, 27, 1) = 'Z')),
  legal_tier TEXT CHECK (legal_tier IS NULL OR legal_tier IN ('green', 'amber', 'stop', 'red'))
) STRICT;
CREATE UNIQUE INDEX meta_sources_key ON meta_sources (url_or_path) WHERE supersedes IS NULL;

CREATE TABLE meta_datasets (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES meta_datasets (id),
  registry_id TEXT NOT NULL,
  source_id TEXT REFERENCES meta_sources (id),
  licence_s1_s2_s3 TEXT,
  pit INTEGER CHECK (pit IS NULL OR pit IN (0, 1))
) STRICT;
CREATE UNIQUE INDEX meta_datasets_key ON meta_datasets (registry_id) WHERE supersedes IS NULL;

CREATE TABLE meta_raw_files (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES meta_raw_files (id),
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  path TEXT NOT NULL,
  original_path TEXT,
  bytes INTEGER NOT NULL CHECK (bytes >= 0),
  tracked_in_git INTEGER NOT NULL CHECK (tracked_in_git IN (0, 1)),
  contains_identifiers INTEGER NOT NULL CHECK (contains_identifiers IN (0, 1)),
  expires_utc TEXT CHECK (expires_utc IS NULL OR (length(expires_utc) = 27 AND substr(expires_utc, 11, 1) = 'T' AND substr(expires_utc, 27, 1) = 'Z')),
  source_id TEXT REFERENCES meta_sources (id),
  dataset_id TEXT REFERENCES meta_datasets (id),
  ingest_run_id TEXT REFERENCES ops_ingest_runs (id),
  CHECK (contains_identifiers = 0 OR expires_utc IS NOT NULL)
) STRICT;
CREATE UNIQUE INDEX meta_raw_files_key ON meta_raw_files (sha256) WHERE supersedes IS NULL;

CREATE TABLE meta_links (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES meta_links (id),
  from_table TEXT NOT NULL,
  from_id TEXT NOT NULL,
  rel TEXT NOT NULL CHECK (rel IN ('claim-source', 'output-revises')),
  to_table TEXT NOT NULL,
  to_id TEXT NOT NULL
) STRICT;

-- ops: rounds, agent runs, ingest runs
CREATE TABLE ops_rounds (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES ops_rounds (id),
  label TEXT NOT NULL,
  goal TEXT
) STRICT;
CREATE UNIQUE INDEX ops_rounds_key ON ops_rounds (label) WHERE supersedes IS NULL;

CREATE TABLE ops_agent_runs (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES ops_agent_runs (id),
  round_id TEXT REFERENCES ops_rounds (id),
  run_ref TEXT,  -- the run log id (AR-YYYYMMDD-NNN)
  persona TEXT NOT NULL,
  kind TEXT CHECK (kind IS NULL OR kind IN ('opinion', 'checker', 'builder', 'researcher')),
  task TEXT NOT NULL,
  goal_id TEXT,
  thread TEXT,
  cap INTEGER CHECK (cap IS NULL OR cap > 0),
  prompt_sha TEXT,
  status TEXT,
  started_utc TEXT CHECK (started_utc IS NULL OR (length(started_utc) = 27 AND substr(started_utc, 11, 1) = 'T' AND substr(started_utc, 27, 1) = 'Z')),
  subagent_tokens INTEGER CHECK (subagent_tokens IS NULL OR subagent_tokens >= 0),
  tool_uses INTEGER CHECK (tool_uses IS NULL OR tool_uses >= 0),
  duration_ms INTEGER CHECK (duration_ms IS NULL OR duration_ms >= 0),
  output_path TEXT,
  verdict TEXT CHECK (verdict IS NULL OR verdict IN ('accepted', 'revised', 'rejected')),
  verdict_note TEXT,
  model TEXT
) STRICT;
CREATE UNIQUE INDEX ops_agent_runs_key ON ops_agent_runs (run_ref) WHERE supersedes IS NULL AND run_ref IS NOT NULL;

CREATE TABLE ops_ingest_runs (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES ops_ingest_runs (id),
  dataset_id TEXT REFERENCES meta_datasets (id),
  job TEXT NOT NULL,
  window_start TEXT,
  window_end TEXT,
  ended_utc TEXT CHECK (ended_utc IS NULL OR (length(ended_utc) = 27 AND substr(ended_utc, 11, 1) = 'T' AND substr(ended_utc, 27, 1) = 'Z')),
  rows INTEGER CHECK (rows IS NULL OR rows >= 0),
  gaps INTEGER CHECK (gaps IS NULL OR gaps >= 0),
  dupes INTEGER CHECK (dupes IS NULL OR dupes >= 0),
  exit_code INTEGER,
  file_sha256 TEXT CHECK (file_sha256 IS NULL OR length(file_sha256) = 64)
) STRICT;

-- knowledge: outputs, claims, verifications, papers, gaps, lessons
CREATE TABLE knowledge_outputs (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES knowledge_outputs (id),
  path TEXT NOT NULL,
  file_sha256 TEXT CHECK (file_sha256 IS NULL OR length(file_sha256) = 64),
  agent_run_id TEXT REFERENCES ops_agent_runs (id),
  round_id TEXT REFERENCES ops_rounds (id),
  verdict TEXT
) STRICT;

CREATE TABLE knowledge_papers (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES knowledge_papers (id),
  doi TEXT NOT NULL,
  source_id TEXT REFERENCES meta_sources (id),
  title TEXT NOT NULL,
  year INTEGER,
  grade TEXT CHECK (grade IS NULL OR grade IN ('A', 'B', 'C'))
) STRICT;
CREATE UNIQUE INDEX knowledge_papers_key ON knowledge_papers (doi) WHERE supersedes IS NULL;

CREATE TABLE knowledge_claims (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES knowledge_claims (id),
  output_id TEXT REFERENCES knowledge_outputs (id),
  paper_id TEXT REFERENCES knowledge_papers (id),
  text TEXT NOT NULL,
  value REAL,
  unit TEXT,
  grade TEXT CHECK (grade IS NULL OR grade IN ('A', 'B', 'C'))
) STRICT;

CREATE TABLE knowledge_verifications (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES knowledge_verifications (id),
  claim_id TEXT REFERENCES knowledge_claims (id),
  output_id TEXT REFERENCES knowledge_outputs (id),
  agent_run_id TEXT REFERENCES ops_agent_runs (id),
  checked_by TEXT NOT NULL,
  method TEXT NOT NULL,
  sample_seed INTEGER,
  verdict TEXT NOT NULL,
  CHECK ((claim_id IS NOT NULL) + (output_id IS NOT NULL) + (agent_run_id IS NOT NULL) = 1)
) STRICT;

CREATE TABLE knowledge_gaps (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES knowledge_gaps (id),
  round_id TEXT REFERENCES ops_rounds (id),
  item TEXT NOT NULL,
  found_by TEXT NOT NULL CHECK (found_by IN ('founder', 'agent')),
  state TEXT NOT NULL,
  root_cause TEXT
) STRICT;

CREATE TABLE knowledge_lessons (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES knowledge_lessons (id),
  what_broke TEXT NOT NULL,
  cost TEXT,
  rule_added TEXT,
  rule_location TEXT,
  agent_run_id TEXT REFERENCES ops_agent_runs (id),
  gap_id TEXT REFERENCES knowledge_gaps (id),
  risk_id TEXT REFERENCES decide_risks (id),
  recurrence_of TEXT REFERENCES knowledge_lessons (id)
) STRICT;

-- decide: decisions, opinions, reviews, forecasts, resolutions, risks
CREATE TABLE decide_decisions (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES decide_decisions (id),
  dl_id TEXT NOT NULL,
  round_id TEXT REFERENCES ops_rounds (id),
  question TEXT NOT NULL,
  choice TEXT NOT NULL,
  grade TEXT,
  expected_effect TEXT,
  review_due_utc TEXT CHECK (review_due_utc IS NULL OR (length(review_due_utc) = 27 AND substr(review_due_utc, 11, 1) = 'T' AND substr(review_due_utc, 27, 1) = 'Z')),
  revisit_trigger TEXT
) STRICT;
CREATE UNIQUE INDEX decide_decisions_key ON decide_decisions (dl_id) WHERE supersedes IS NULL;

CREATE TABLE decide_opinions (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES decide_opinions (id),
  decision_id TEXT NOT NULL REFERENCES decide_decisions (id),
  persona TEXT NOT NULL,
  round_no INTEGER CHECK (round_no IS NULL OR round_no >= 1),
  stance TEXT NOT NULL,
  output_id TEXT REFERENCES knowledge_outputs (id)
) STRICT;

CREATE TABLE decide_decision_reviews (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES decide_decision_reviews (id),
  decision_id TEXT NOT NULL REFERENCES decide_decisions (id),
  expected TEXT,
  actual TEXT,
  verdict TEXT NOT NULL
) STRICT;

CREATE TABLE decide_forecasts (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES decide_forecasts (id),
  decision_id TEXT REFERENCES decide_decisions (id),
  agent_run_id TEXT REFERENCES ops_agent_runs (id),
  claim TEXT NOT NULL,
  p REAL NOT NULL CHECK (p BETWEEN 0 AND 1),
  resolve_by_utc TEXT CHECK (resolve_by_utc IS NULL OR (length(resolve_by_utc) = 27 AND substr(resolve_by_utc, 11, 1) = 'T' AND substr(resolve_by_utc, 27, 1) = 'Z'))
) STRICT;

CREATE TABLE decide_forecast_resolutions (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES decide_forecast_resolutions (id),
  forecast_id TEXT NOT NULL REFERENCES decide_forecasts (id),
  outcome INTEGER NOT NULL CHECK (outcome IN (0, 1)),
  evidence TEXT NOT NULL
) STRICT;
CREATE UNIQUE INDEX decide_forecast_resolutions_key ON decide_forecast_resolutions (forecast_id) WHERE supersedes IS NULL;

CREATE TABLE decide_risks (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES decide_risks (id),
  risk_no TEXT NOT NULL,
  decision_id TEXT REFERENCES decide_decisions (id),
  title TEXT NOT NULL,
  stage TEXT,
  likelihood_yr REAL CHECK (likelihood_yr IS NULL OR likelihood_yr BETWEEN 0 AND 1),
  el_eur_yr REAL,
  auto_check TEXT,
  status TEXT
) STRICT;
CREATE UNIQUE INDEX decide_risks_key ON decide_risks (risk_no) WHERE supersedes IS NULL;

-- research: preregistrations, runs, trials, gate lines, N_eff
CREATE TABLE research_preregistrations (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES research_preregistrations (id),
  path TEXT NOT NULL,
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  frozen_utc TEXT NOT NULL CHECK (length(frozen_utc) = 27 AND substr(frozen_utc, 11, 1) = 'T' AND substr(frozen_utc, 27, 1) = 'Z')
) STRICT;
CREATE UNIQUE INDEX research_preregistrations_key ON research_preregistrations (sha256) WHERE supersedes IS NULL;

CREATE TABLE research_runs (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES research_runs (id),
  registered_utc TEXT NOT NULL CHECK (length(registered_utc) = 27 AND substr(registered_utc, 11, 1) = 'T' AND substr(registered_utc, 20, 1) = '.' AND substr(registered_utc, 27, 1) = 'Z'),
  run_kind TEXT NOT NULL CHECK (run_kind IN ('gate', 'm2', 'lab', 'exploratory', 'lab_backfill')),
  withdrawn INTEGER NOT NULL DEFAULT 0 CHECK (withdrawn IN (0, 1)),
  code_sha256 TEXT,
  config_sha256 TEXT,
  data_sha256 TEXT,
  prereg_id TEXT REFERENCES research_preregistrations (id),
  touches_reserved INTEGER NOT NULL CHECK (touches_reserved IN (0, 1)),
  reserved_list_sha256 TEXT,
  planned_cells INTEGER NOT NULL CHECK (planned_cells >= 0),
  window TEXT,
  assets_json TEXT,
  seed INTEGER,
  cost_bps_side REAL,
  agent_run_id TEXT REFERENCES ops_agent_runs (id),
  UNIQUE (id, registered_utc),
  CHECK (prereg_id IS NOT NULL OR run_kind = 'lab_backfill'
         OR (run_kind IN ('lab', 'exploratory') AND touches_reserved = 0))
) STRICT;

CREATE TABLE research_trials (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES research_trials (id),
  trial_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  registered_utc TEXT NOT NULL,
  result_utc TEXT NOT NULL CHECK (length(result_utc) = 27 AND substr(result_utc, 11, 1) = 'T' AND substr(result_utc, 20, 1) = '.' AND substr(result_utc, 27, 1) = 'Z'),
  family TEXT,
  rule TEXT,
  asset TEXT,
  window TEXT,
  params_json TEXT,
  row_json TEXT NOT NULL,
  config_sha256 TEXT,
  code_sha256 TEXT,
  data_sha256 TEXT,
  provenance TEXT NOT NULL CHECK (provenance IN ('full', 'legacy')),
  reserved INTEGER NOT NULL DEFAULT 0 CHECK (reserved IN (0, 1)),
  status TEXT,
  cost_bps_side REAL,
  seed INTEGER,
  sharpe_net REAL,
  sharpe_bh REAL,
  result_sha256 TEXT,
  backfilled INTEGER NOT NULL DEFAULT 0 CHECK (backfilled IN (0, 1)),
  withdrawn INTEGER NOT NULL DEFAULT 0 CHECK (withdrawn IN (0, 1)),
  FOREIGN KEY (run_id, registered_utc) REFERENCES research_runs (id, registered_utc),
  CHECK (backfilled = 1 OR registered_utc < result_utc),
  CHECK (provenance = 'legacy' OR (code_sha256 IS NOT NULL AND data_sha256 IS NOT NULL))
) STRICT;
CREATE UNIQUE INDEX research_trials_key ON research_trials (trial_id) WHERE supersedes IS NULL AND withdrawn = 0;
CREATE INDEX research_trials_run ON research_trials (run_id);

CREATE TABLE research_gate_lines (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES research_gate_lines (id),
  source_file TEXT NOT NULL,
  seq INTEGER NOT NULL CHECK (seq >= 0),
  trial_id TEXT,
  line_json TEXT NOT NULL,
  line_sha256 TEXT NOT NULL CHECK (length(line_sha256) = 64),
  prereg_id TEXT REFERENCES research_preregistrations (id),
  UNIQUE (source_file, seq)
) STRICT;

CREATE TABLE research_n_eff (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES research_n_eff (id),
  decision_id TEXT REFERENCES decide_decisions (id),
  method TEXT NOT NULL,
  input_hashes_json TEXT NOT NULL,
  value REAL NOT NULL CHECK (value >= 1),
  version INTEGER NOT NULL CHECK (version >= 1)
) STRICT;

CREATE TABLE research_n_eff_runs (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES research_n_eff_runs (id),
  n_eff_id TEXT NOT NULL REFERENCES research_n_eff (id),
  run_id TEXT NOT NULL REFERENCES research_runs (id)
) STRICT;
CREATE UNIQUE INDEX research_n_eff_runs_key ON research_n_eff_runs (n_eff_id, run_id) WHERE supersedes IS NULL;

-- traders: platforms, snapshots (Parquet index), watchlist, cohorts
CREATE TABLE traders_platforms (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES traders_platforms (id),
  name TEXT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('cex', 'dex')),
  route TEXT CHECK (route IS NULL OR route IN ('R1', 'R2', 'R3')),
  barred INTEGER NOT NULL DEFAULT 0 CHECK (barred IN (0, 1))
) STRICT;
CREATE UNIQUE INDEX traders_platforms_key ON traders_platforms (name) WHERE supersedes IS NULL;

CREATE TABLE traders_snapshots (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES traders_snapshots (id),
  platform_id TEXT NOT NULL REFERENCES traders_platforms (id),
  market TEXT,
  src_window TEXT,
  snapshot_utc TEXT NOT NULL CHECK (length(snapshot_utc) = 27 AND substr(snapshot_utc, 11, 1) = 'T' AND substr(snapshot_utc, 27, 1) = 'Z'),
  parquet_path TEXT NOT NULL,
  rows INTEGER CHECK (rows IS NULL OR rows >= 0),
  sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
  source_sha256 TEXT CHECK (source_sha256 IS NULL OR length(source_sha256) = 64),
  ingest_run_id TEXT REFERENCES ops_ingest_runs (id),
  raw_file_id TEXT REFERENCES meta_raw_files (id)
) STRICT;
CREATE UNIQUE INDEX traders_snapshots_key ON traders_snapshots (sha256) WHERE supersedes IS NULL;

CREATE TABLE traders_watchlist (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES traders_watchlist (id),
  build_utc TEXT NOT NULL CHECK (length(build_utc) = 27 AND substr(build_utc, 11, 1) = 'T' AND substr(build_utc, 27, 1) = 'Z'),
  rule_version TEXT NOT NULL,
  leader_hash TEXT NOT NULL CHECK (leader_hash <> ''),
  platform_id TEXT NOT NULL REFERENCES traders_platforms (id),
  snapshot_id TEXT REFERENCES traders_snapshots (id),
  gates_json TEXT
) STRICT;

CREATE TABLE traders_cohorts (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES traders_cohorts (id),
  name_version TEXT NOT NULL,
  rule_sha256 TEXT NOT NULL CHECK (length(rule_sha256) = 64),
  frozen_utc TEXT NOT NULL CHECK (length(frozen_utc) = 27 AND substr(frozen_utc, 11, 1) = 'T' AND substr(frozen_utc, 27, 1) = 'Z')
) STRICT;
CREATE UNIQUE INDEX traders_cohorts_key ON traders_cohorts (name_version) WHERE supersedes IS NULL;

CREATE TABLE traders_cohort_members (
  id TEXT PRIMARY KEY,
  created_utc TEXT NOT NULL CHECK (length(created_utc) = 27 AND substr(created_utc, 11, 1) = 'T' AND substr(created_utc, 20, 1) = '.' AND substr(created_utc, 27, 1) = 'Z'),
  source TEXT NOT NULL CHECK (source <> ''),
  owner TEXT NOT NULL,
  supersedes TEXT REFERENCES traders_cohort_members (id),
  cohort_id TEXT NOT NULL REFERENCES traders_cohorts (id),
  leader_hash TEXT NOT NULL CHECK (leader_hash <> ''),
  platform_id TEXT NOT NULL REFERENCES traders_platforms (id)
) STRICT;
CREATE UNIQUE INDEX traders_cohort_members_key ON traders_cohort_members (cohort_id, leader_hash, platform_id) WHERE supersedes IS NULL;
