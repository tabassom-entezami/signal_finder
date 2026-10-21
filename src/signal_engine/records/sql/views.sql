-- Derived views, rebuilt by `migrate` after the tables and the per-table template (`*_current`).
-- Counting reads the base tables, never `*_current`: a superseded or withdrawn trial is still a try.
-- The date arithmetic in the score views is SQLite's; it is the one part to rewrite at a Postgres port.

DROP VIEW IF EXISTS research_runs_status;
CREATE VIEW research_runs_status AS
SELECT r.id, r.run_kind, r.withdrawn, r.planned_cells, r.registered_utc,
  (SELECT count(*) FROM research_trials AS t WHERE t.run_id = r.id) AS n_rows,
  CASE WHEN EXISTS (SELECT 1 FROM research_trials AS t WHERE t.run_id = r.id) THEN 'done' ELSE 'aborted' END
    AS status
FROM research_runs_current AS r;

-- The technique-lab rule: skipped runs are not tries; trader-trait fits are not strategy tries (the family
-- list equals NOT_IN_DSR_FAMILIES in research_db.py; a test keeps them equal).
DROP VIEW IF EXISTS research_lab_dsr;
CREATE VIEW research_lab_dsr AS
SELECT t.*, r.run_kind,
  CASE WHEN substr(coalesce(t.status, ''), 1, 7) <> 'skipped'
        AND coalesce(t.family, '') NOT IN ('trader-traits') THEN 1 ELSE 0 END AS counts_in_dsr
FROM research_trials AS t JOIN research_runs AS r ON r.id = t.run_id;

-- N counts distinct trial ids (a re-run of the same id is one try) plus one per aborted run; N_upper adds
-- the planned cells of the aborted runs instead. m2_n is the m2 part of the gate N (the gate log part is
-- validation.trials.n_trials, added in Python).
DROP VIEW IF EXISTS research_n;
CREATE VIEW research_n AS
SELECT
  (SELECT count(DISTINCT trial_id) FROM research_lab_dsr
    WHERE counts_in_dsr = 1 AND run_kind IN ('lab', 'lab_backfill', 'exploratory'))
  + (SELECT count(*) FROM research_runs_status
    WHERE status = 'aborted' AND run_kind IN ('lab', 'lab_backfill', 'exploratory')) AS lab_n,
  (SELECT count(DISTINCT trial_id) FROM research_lab_dsr
    WHERE counts_in_dsr = 1 AND run_kind IN ('lab', 'lab_backfill', 'exploratory'))
  + (SELECT coalesce(sum(planned_cells), 0) FROM research_runs_status
    WHERE status = 'aborted' AND run_kind IN ('lab', 'lab_backfill', 'exploratory')) AS lab_n_upper,
  (SELECT count(DISTINCT trial_id) FROM research_trials AS t JOIN research_runs AS r ON r.id = t.run_id
    WHERE r.run_kind = 'm2')
  + (SELECT count(*) FROM research_runs_status WHERE status = 'aborted' AND run_kind = 'm2') AS m2_n;

-- The only trials a gate may read: full provenance, run live (not backfilled), not withdrawn.
DROP VIEW IF EXISTS research_evidence;
CREATE VIEW research_evidence AS
SELECT * FROM research_trials_current WHERE provenance <> 'legacy' AND backfilled = 0 AND withdrawn = 0;

-- Scoreboard: is the team improving over time?
-- Medians with window functions (SQLite has no median()): the middle row, or the mean of the two middle
-- rows, of the non-null values in each week and persona.
DROP VIEW IF EXISTS score_agent_runs;
CREATE VIEW score_agent_runs AS
WITH r AS (
  SELECT strftime('%Y-W%W', coalesce(started_utc, created_utc)) AS week, persona, verdict,
    subagent_tokens AS tokens, tool_uses, duration_ms / 60000.0 AS minutes
  FROM ops_agent_runs_current
), tok AS (
  SELECT week, persona, tokens AS v, row_number() OVER (PARTITION BY week, persona ORDER BY tokens) AS i,
    count(*) OVER (PARTITION BY week, persona) AS n
  FROM r WHERE tokens IS NOT NULL
), tools AS (
  SELECT week, persona, tool_uses AS v, row_number() OVER (PARTITION BY week, persona ORDER BY tool_uses) AS i,
    count(*) OVER (PARTITION BY week, persona) AS n
  FROM r WHERE tool_uses IS NOT NULL
), mins AS (
  SELECT week, persona, minutes AS v, row_number() OVER (PARTITION BY week, persona ORDER BY minutes) AS i,
    count(*) OVER (PARTITION BY week, persona) AS n
  FROM r WHERE minutes IS NOT NULL
)
SELECT r.week, r.persona, count(*) AS runs,
  (SELECT avg(m.v) FROM tok AS m WHERE m.week = r.week AND m.persona = r.persona
    AND m.i IN ((m.n + 1) / 2, (m.n + 2) / 2)) AS median_tokens,
  (SELECT avg(m.v) FROM tools AS m WHERE m.week = r.week AND m.persona = r.persona
    AND m.i IN ((m.n + 1) / 2, (m.n + 2) / 2)) AS median_tool_uses,
  (SELECT avg(m.v) FROM mins AS m WHERE m.week = r.week AND m.persona = r.persona
    AND m.i IN ((m.n + 1) / 2, (m.n + 2) / 2)) AS median_minutes,
  100.0 * sum(CASE WHEN r.verdict IN ('accepted', 'revised') THEN 1 ELSE 0 END) / count(*) AS usable_pct
FROM r
GROUP BY r.week, r.persona;

-- Verifications by verdict class (set by the checker): the verdict grades the author's output, so the owner
-- reads how many outputs are verified (pass / partial / fail) and how many are not yet (unverified backlog).
-- pass = plain "decision-grade" (or CONFIRMED / PASS / OK); partial = "usable …" or a scoped "decision-grade
-- for …"; fail = "not usable"; unverified = only the author checked it, or the output is missing.
DROP VIEW IF EXISTS score_verification;
CREATE VIEW score_verification AS
WITH v AS (
  SELECT CASE
    WHEN lower(checked_by) IN ('author only', 'missing', '—', '-', '') OR lower(substr(checked_by, 1, 11)) = 'author only'
      OR lower(verdict) = 'missing' THEN 'unverified'
    WHEN lower(substr(verdict, 1, 10)) = 'not usable' THEN 'fail'
    WHEN lower(substr(verdict, 1, 18)) = 'decision-grade for' THEN 'partial'
    WHEN lower(substr(verdict, 1, 14)) = 'decision-grade' OR upper(verdict) IN ('CONFIRMED', 'PASS', 'OK') THEN 'pass'
    WHEN lower(substr(verdict, 1, 6)) = 'usable' THEN 'partial'
    ELSE 'other' END AS class
  FROM knowledge_verifications_current
)
SELECT class, count(*) AS verifications
FROM v
GROUP BY class;

DROP VIEW IF EXISTS score_brier;
CREATE VIEW score_brier AS
SELECT coalesce(a.persona, f.owner) AS forecaster, substr(r.created_utc, 1, 7) AS month, count(*) AS n,
  avg((f.p - r.outcome) * (f.p - r.outcome)) AS brier, 0.25 AS brier_always_half
FROM decide_forecasts_current AS f
JOIN decide_forecast_resolutions_current AS r ON r.forecast_id = f.id
LEFT JOIN ops_agent_runs AS a ON a.id = f.agent_run_id
WHERE lower(trim(f.source)) <> 'backfill' -- a backfilled p is ex-post by construction: kept, never calibrated
GROUP BY coalesce(a.persona, f.owner), substr(r.created_utc, 1, 7);

DROP VIEW IF EXISTS score_decision_reviews;
CREATE VIEW score_decision_reviews AS
SELECT
  (SELECT count(*) FROM decide_decisions_current AS d
    WHERE d.review_due_utc IS NOT NULL
      AND replace(substr(d.review_due_utc, 1, 19), 'T', ' ') < datetime('now')
      AND NOT EXISTS (SELECT 1 FROM decide_decision_reviews_current AS v WHERE v.decision_id = d.id)) AS overdue,
  (SELECT count(*) FROM decide_decision_reviews_current) AS reviews,
  (SELECT 100.0 * sum(CASE WHEN verdict = 'hit' THEN 1 ELSE 0 END) / count(*)
    FROM decide_decision_reviews_current) AS hit_pct;

DROP VIEW IF EXISTS score_ingest;
CREATE VIEW score_ingest AS
SELECT coalesce(dataset_id, job) AS dataset, count(*) AS runs, coalesce(sum(gaps), 0) AS gaps,
  coalesce(sum(dupes), 0) AS dupes, sum(CASE WHEN exit_code <> 0 THEN 1 ELSE 0 END) AS failed
FROM ops_ingest_runs_current
WHERE replace(substr(created_utc, 1, 19), 'T', ' ') >= datetime('now', '-30 days')
GROUP BY coalesce(dataset_id, job);

DROP VIEW IF EXISTS score_lessons;
CREATE VIEW score_lessons AS
SELECT substr(created_utc, 1, 7) AS month, count(*) AS lessons,
  sum(CASE WHEN recurrence_of IS NOT NULL THEN 1 ELSE 0 END) AS recurrences
FROM knowledge_lessons_current
GROUP BY substr(created_utc, 1, 7);

DROP VIEW IF EXISTS score_gaps;
CREATE VIEW score_gaps AS
SELECT count(*) AS gaps,
  sum(CASE WHEN lower(state) NOT IN ('closed', 'done', 'fixed', 'resolved') THEN 1 ELSE 0 END) AS open,
  100.0 * sum(CASE WHEN found_by = 'founder' THEN 1 ELSE 0 END) / count(*) AS founder_pct
FROM knowledge_gaps_current;

DROP VIEW IF EXISTS score_trials;
CREATE VIEW score_trials AS
SELECT s.run_kind, count(*) AS runs, sum(CASE WHEN s.status = 'aborted' THEN 1 ELSE 0 END) AS aborted,
  (SELECT count(DISTINCT t.trial_id) FROM research_trials AS t JOIN research_runs AS r ON r.id = t.run_id
    WHERE r.run_kind = s.run_kind) AS trials,
  (SELECT 100.0 * sum(CASE WHEN t.provenance = 'full' THEN 1 ELSE 0 END) / count(*)
    FROM research_trials AS t JOIN research_runs AS r ON r.id = t.run_id WHERE r.run_kind = s.run_kind)
    AS full_provenance_pct
FROM research_runs_status AS s
GROUP BY s.run_kind;
