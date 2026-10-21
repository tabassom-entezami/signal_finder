# signal_finder

A crypto swing-trading signal engine (BTC/ETH, daily bars; four-hour bars are planned, not built). Deterministic quant code computes the regime, the score
and the risk of every signal; a language model is planned to act only as an evidence-checked meta-labeler on
top, and only if it beats the code alone. Signals are generic, versioned and meant to be published with a public
track record.

**Status:** research stage; nothing is traded. This repository holds the generic engine and its tests: data
fetchers and recorders, storage and a paper simulator. The first frozen strategy failed its
pre-registered out-of-sample test after realistic execution costs, so it was not deployed; the result is
published in [RESULTS.md](RESULTS.md), and the code that produced it is here too: the frozen strategy rule,
the backtest loop, the validation statistics, the point-in-time loader and the append-only records store. Data,
the frozen rules file, research notes, internal decision records and the owner's own account values (kept in
`.env`) stay private.

**Author:** Tabassom Entezami

## Requirements

- Python 3.13 (pinned in `.python-version`)
- [uv](https://docs.astral.sh/uv/)

## Setup

```bash
git clone https://github.com/tabassom-entezami/signal_finder.git
cd signal_finder
uv sync            # creates .venv with Python 3.13 and installs the locked dependencies
```

## Run

```bash
uv run python -m signal_engine --version
uv run python -m signal_engine fetch-candles --symbol BTC/EUR --since 2020-01-01 [--data-dir PATH]
#   closed Bitvavo daily candles (public API, no key) -> data/raw/bitvavo/<SYM>/<yyyy>/<mm>/part-0.parquet;
#   re-runs keep stored bars (point-in-time); a changed re-fetch is logged and kept; gaps are logged, never filled
uv run python -m signal_engine fetch-ecb --start 2019-03-01 --end <date> [--data-dir PATH]
#   ECB EUR/USD reference rates
uv run python -m signal_engine record-books --symbols BTC/EUR,BTC/USDC --interval 5 --start <ISO UTC> [--end <ISO UTC>]
#   records public order books every few seconds; a failed fetch is an error row, the run continues
STAKE_USD=1000 P98_MAX_USD=100 SAVINGS_PCT=0.03 OP_EVENTS='[[0.01, 0.1, 0.5]]' \
  uv run python -m signal_engine reproduce-dev --seed 7 --out dev.json
#   the whole first-test path (rule, backtest, gates, Monte Carlo) on synthetic bars with demo account values;
#   no real data, rules file or .env needed; expect result_sha256 0b87ed0e3d3dffd496dba03cc94feb58d044b44fa8633c2662aceff34df19270
```

Data is written under `data/` (git-ignored).

## Test

```bash
uv run pytest          # tests
uv run ruff check .    # lint
uv run mypy src tests  # typecheck
```

## Project structure

- `src/signal_engine/` — the generic engine: settings, JSON logging, Parquet/DuckDB storage, data fetchers
  and recorders (Bitvavo candles and order books, ECB rates, CFTC COT, market listings, news feeds), the
  trade outcome resolver and a paper simulator that fills against real order books

  Exchange names here identify public market-data APIs these tools read. They are not an endorsement, an
  affiliation or a statement of where anyone trades.
- `tests/` — pytest suite (runs offline)

## Configuration

Copy `.env.example` to `.env`. Every setting is optional: `LOG_LEVEL` (default `INFO`), `DATA_DIR` (default `data`).

## More

- [RESULTS.md](RESULTS.md): the first out-of-sample test, a FAIL, reported in full
- [ROADMAP.md](ROADMAP.md): where the project stands, what comes next and the hours spent so far
- [ARCHITECTURE.md](ARCHITECTURE.md): how the system fits together and which parts are public
- [HOW_WE_USE_AI.md](HOW_WE_USE_AI.md): the role of Claude in the product
- [BUILT_WITH_CLAUDE.md](BUILT_WITH_CLAUDE.md): how the project is built with Claude Code
- [AGENT_TEAM.md](AGENT_TEAM.md): the agent team by name, how they work together, their safety rules and how the owner uses them

## How this was built: an AI agent team

The project is built by one founder working with a team of about thirty specialist AI agents (Claude), each
with one role and a written standard to work to:

- **Quant research and development:** strategy research, quant development, portfolio allocation, validation.
- **Data:** data engineering and data-source research.
- **Markets:** trading practice, market microstructure, on-chain activity, macro events and news.
- **Risk and safety:** risk management, security engineering, operational security.
- **Law and money:** legal, regulatory, tax and finance research.
- **Product:** customer and product research.
- **Running the team:** a team manager, QA and testing, DevOps, a report analyst and an agent-quality auditor.

How work gets decided:

1. The specialists related to a question each write an opinion into one shared discussion.
2. They read each other and reply by name: agree, disagree with evidence, or merge proposals.
3. A checker who did not write the claims re-checks them on real data, using a different method from the authors.
4. Only then is a decision recorded and built. Values, legal judgement and money stay with the founder.

Safety layers around the agents:

- An operating-system sandbox limits what any command can read, write or reach on the network.
- A guard checks every agent action before it runs. It blocks network and secret access, enforces a tool-call
  budget per task, and lets changes to the safety controls through only after a security review or the
  founder's approval.
- Only the founder writes to git.

The agents' prompts, discussions, decision records and research stay private. This section describes how they
work, not what they contain.

## License

All rights reserved for now: the code is public to read, but do not auto use it, at least cost you one star to repo:).
