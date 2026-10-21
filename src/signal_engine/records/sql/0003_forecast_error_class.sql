-- Why a resolved forecast missed, from a closed list applied in order (the first test that fires is the class):
-- unscorable (backfill, unresolved or missing outcome data), data (inputs differ as of the decision time),
-- code (a second implementation or a recompute from raw disagrees, or a planted edge is not recovered),
-- cost (right sign gross, wrong sign net, or realised cost above 2x the model), regime (a detector logged at
-- forecast time fired before the outcome), assumption (decided for a group of forecasts only: every test above
-- is clear and calibration fails) and noise (any other miss). NULL on lines written before this column.
ALTER TABLE decide_forecast_resolutions ADD COLUMN error_class TEXT CHECK (error_class IS NULL OR error_class IN
  ('unscorable', 'data', 'code', 'cost', 'regime', 'assumption', 'noise'));
