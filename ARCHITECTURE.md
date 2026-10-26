 # Architecture

`signal_finder` is the open part of a crypto swing-signal engine for daily bars (four-hour bars are planned, not built). This document explains how the whole system fits together, which parts live in this repository, and the rules every part follows.

## 1. What the system does, end to end

```
 public sources                    this repo                         private
 --------------                    ---------                         -------
 Bitvavo candles  --+
 Bitvavo books    --+--> fetchers / recorders --> point-in-time --> features --> signal
 ECB EUR/USD      --+    (append-only Parquet)    store (DuckDB)                   |
 CFTC COT         --+                                                              v
 market listings  --+                                     optional label (language model,
 news feeds       --+                                     see HOW_WE_USE_AI.md) -> validators
                                                                    (code) -> risk (size, stop)
                                                                                   |
                                         record (committed BEFORE the outcome) <---+
                                                   |
                                                   v
                         paper simulator (fills on real order books)  -- now
                         live execution on the venue                  -- later
                                                   |
                                                   v
                         outcome resolver --> scored record --> published track record
```

1. **Data in.** Fetchers pull closed history; recorders capture live public data forward in time.
2. **Point-in-time store.** Every row says when it first became known. Nothing is overwritten.
3. **Signals.** Features and a strategy turn closed bars into a target position with a probability and a horizon.
4. **Risk.** Deterministic rules set the size and the stop.
5. **Record.** Each prediction is written to an append-only record before its outcome exists.
6. **Paper or live.** The paper simulator fills against recorded order books. Live trading, when it comes, uses the same decision path.
7. **Published record.** One outcome resolver scores every prediction the same way, and the scores are published.

## 2. What is in this repository and what is private

**In this repository (the generic engine):**
- runtime settings from environment variables and an optional `.env` file;
- structured JSON logging (one object per line);
- Parquet storage with DuckDB reads;
- fetchers and recorders: Bitvavo daily candles, Bitvavo public order books and market status, ECB EUR/USD reference rates, CFTC Commitments of Traders for the CME bitcoin and ether futures, Bitvavo market and asset listings with daily diffs, and news/status/regulator feeds;
- the outcome resolver;
- the paper simulator and its order-book fill model;
- the code behind the first out-of-sample test: the frozen momentum rule, the backtest loop and synthetic test data, the validation statistics (PSR, DSR, PBO, the loss Monte Carlo, the trial log), the point-in-time loader with its lock, the currency conversion join and the `m1-oos` command;
- the append-only records store (SQLite, migrations, triggers, views) and its core `records` commands;
- tests for all of the above.

**Private (by role):** the frozen rules file, the reserved-coin rule, the warm-up data source, the records importers and their key, the research database, the prediction record and scorecard, and operations scripts. A public module that needs one of them says so in a comment at its import and asks you to create your own; the command-line tool registers a command only when its modules are present, so this repository runs on its own. The owner's own values (stake, loss limit, savings rate, operational events, default owners) are read from `.env` (see `.env.example`); nothing personal is in the code.

## 3. Data layout and point-in-time rules

**Layout.**
- Historical series: `data/raw/<source>/<symbol>/<yyyy>/<mm>/` Parquet partitions.
- Forward recorders: one file per UTC day, `data/raw/<source>/<yyyy-mm-dd>.parquet`, append-only.
- Order books: one file per tick under `data/raw/bitvavo_books/<yyyy>/<mm>/<dd>/<hh>/`.
- Every write goes to a hidden temporary name and is then renamed, so a reader never sees half a file.
- DuckDB reads the Parquet files directly; there is no database server.
- Timestamps are integer milliseconds, UTC only.

**Point-in-time rules.**
- **Closed bars only.** Venues return the bar that is still forming; it is dropped.
- **Every row carries the time it became known.** Candles and reference rates carry `first_available_time` (first ingest, never changed). News, COT and listings carry `known_at` plus `known_at_type`: the publisher's own time, our observation time, or (for event-day-only sources) our observation time, never the earlier event day.
- **A decision at time t reads only rows known at or before t.**
- **Gaps are reported, never filled.** A gap in an input window means no decision for that symbol.
- **History is not rewritten.** A closed bar that changes on a later fetch is logged as a revision, not silently replaced.
- **No personal data.** Author names, usernames, avatars and addresses are dropped at fetch time; news rows keep no prices.

**How the data is checked.**
- **A registry row before any use.** Each dataset has one row: which project stage its licence allows, whether it is point-in-time safe, where it is stored, its owner, its refresh cadence and a better source to watch. A dataset without a row is not used, and one that is not point-in-time safe is not used in a backtest. A check fails on a missing licence or point-in-time value, on an active dataset that is not point-in-time safe, or on an owner that is not a real role, and runs after every work round.
- **Fresh, without reading prices.** A refresh job updates what is due, then warns when a dataset is behind (it reads only timestamps) or when its licence was last read too long ago. A better source replaces a row only after it is measured.
- **Held-out data is locked.** The loader opens test data only when the hash of the frozen rules file matches its recorded hash, so the rules cannot change after the data is seen.
- **Times are explicit.** A timestamp without a timezone is refused.
- **Records cannot be quietly edited.** The records store is append-only, and every append is sealed by a line in a hash-chained manifest; its check verifies integrity, the chain and that every rendered report still matches the stored rows, and a second check fails when the store drifts from its source files.
- **Numbers are recomputed by someone else.** Before a report is used, a checker who did not write it recomputes its numbers from the raw data with a different method (see [HOW_WE_WORK.md](HOW_WE_WORK.md)).

## 4. How the recorders work

- **Order-book recorder.** On a fixed time grid it stores the top levels of each symbol's public book plus the venue's market status string ("trading", "halted", "auction", ...). It is built to capture maintenance halts and reopens. A failed fetch never stops the run; it becomes a row with an `error` field.
- **Listings recorder.** A daily snapshot of all markets and assets, diffed against the previous snapshot: listings, delistings, status and field changes.
- **COT recorder.** Weekly CFTC data with its real release time, not the report date.
- **News recorder.** Each run appends only items it has not stored before.
- **Network.** Public endpoints only, no API key. Plain HTTPS with a timeout and a short retry on transient errors; an optional host allow-list file limits where jobs may connect.

## 5. How the paper simulator works

- **Pure state machine.** It does no I/O. The caller passes books, candles and the clock; a separate loop fetches them. This makes every run replayable from stored data.
- **Book-walk fills.** A market order walks the recorded book from the best price and stops at the venue's market-order slippage protection. What is left is a residual. The simulator never fills at a price the book did not show.
- **Invariant.** After every call, each open position either has an armed stop or is a residual waiting for a prompt sell. Any other state raises an error.
- **Entry refusals.** No entry when the spread is too wide, the book is too thin for the order, maintenance is near, or a bar close is about to happen.
- **Fees.** Venue fees are applied to every leg.
- **One resolver.** Exits observed on candles are decided by the same resolver that scores backtests and the published record. Inside one bar the order is fixed: a gap through the stop, then a stop touch (which wins even if the target was also touched), then the target, then the time stop. Time-outs and open positions are scored like every other outcome and never dropped from a total.

## 6. Deterministic code owns every number; a language model only labels

Prices, sizes, stops, probabilities and scores come from versioned, tested code. A language model may later be used as a labeler: it reads the evidence a deterministic signal produced and returns a label or a veto. It never sets a price, a size or a stop, and it cannot create a signal on its own. Its output, model version and prompt version are recorded like any other input, so its effect can be measured and switched off.

## 7. Reproducibility

Each prediction in the record stores the hash of its exact input bars, its decision time, the version of every component (strategy, features, risk, resolver, data schema), the code commit and a hash of its parameters. It is committed before its outcome. A cold reader can re-read the raw store as of that time, recompute the hash and the decision, and get the same answer; any mismatch is a failure.

## 8. Safety and operations principles

- **Paper first.** In paper mode there are no exchange keys and no trading bot, so there is nothing to steal and no command channel to hijack.
- **Live, later, follows the same rules.** The stop rests on the venue, so a position stays protected if the host dies. The trading key cannot withdraw. Any remote channel can only halt new entries or flatten, never trade, and flattening also works by hand without it. Nothing restarts trading automatically.
- **One clock.** Every timestamp and log line is UTC from one source.
- **Missed is visible.** A day without a decision is logged as "no decision" and never back-filled. Gaps stay gaps.
- **Watched jobs.** Each scheduled job reports start, success and failure to an outside dead-man switch, so a hang or a sleeping host is noticed.
- **Fetched text is data.** Nothing read from a feed or an API is ever run as an instruction.
- **Simple stack.** Python with uv, pytest, ruff and strict mypy, Parquet and DuckDB, one machine. A component is built only when the step that needs it starts.
