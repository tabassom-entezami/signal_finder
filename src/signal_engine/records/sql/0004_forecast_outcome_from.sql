-- The earliest UTC instant at which any data that decides a forecast's outcome exists: the start of the outcome
-- window, the open of the first scored bar for a price claim, or the scheduled start for an event claim. A
-- forecast is scored only when it was created before this instant; NULL on lines written before this column
-- (never scored) and allowed on backfilled lines (never scored either).
ALTER TABLE decide_forecasts ADD COLUMN outcome_from_utc TEXT CHECK (outcome_from_utc IS NULL OR
  (length(outcome_from_utc) = 27 AND substr(outcome_from_utc, 11, 1) = 'T'
    AND substr(outcome_from_utc, 20, 1) = '.' AND substr(outcome_from_utc, 27, 1) = 'Z'));
