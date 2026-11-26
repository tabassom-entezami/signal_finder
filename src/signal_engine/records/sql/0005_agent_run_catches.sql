-- How many catches a checker run records and how many rulings those catches hold in total, as read from the
-- run's source line. Both only ever grow, so a later row of one run with a lower count, or a count that goes
-- back to NULL, means a catch or a ruling was deleted at the source; the superseded rows keep the higher count.
-- NULL on lines without a catches list (every run before this column and every run that is no checker).
ALTER TABLE ops_agent_runs ADD COLUMN catches INTEGER CHECK (catches IS NULL OR catches >= 0);
ALTER TABLE ops_agent_runs ADD COLUMN rulings INTEGER CHECK (rulings IS NULL OR rulings >= 0);
