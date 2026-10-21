-- Per-table template, applied by `migrate` to every table in meta_tables ({t} = the table name), so no
-- table can escape it: refuse UPDATE and DELETE, allow a row to be superseded once, and show the rows
-- nothing supersedes.
CREATE TRIGGER {t}_no_update BEFORE UPDATE ON {t} BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE TRIGGER {t}_no_delete BEFORE DELETE ON {t} BEGIN SELECT RAISE(ABORT, 'append-only'); END;
CREATE UNIQUE INDEX {t}_one_successor ON {t} (supersedes);
CREATE VIEW {t}_current AS SELECT * FROM {t} AS x WHERE NOT EXISTS (SELECT 1 FROM {t} AS y WHERE y.supersedes = x.id);
