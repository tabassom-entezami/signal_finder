# Process tools kit

Small command-line checks we use to keep a multi-agent research process honest. They are a
snapshot of tools from a solo project, rewritten as generic examples: the live copies keep
changing and are not published. Nothing here makes a claim about results; the tools only check
that a written process was followed.

Every tool is one Python file, standard library only, no network, no subprocess. All of them
only read the files you pass, except `run_log.py`, which writes the log file you name.
Each prints `PASS` or `FAIL` lines and exits 0 (pass) or 1 (fail), so it fits a CI step or a
pre-commit hook. `python3 tools/<tool>.py --help` shows every option.

| Tool | What it checks | Example |
|---|---|---|
| `thread_lint.py` | In a Markdown discussion thread (sections `### <Author>: <label>`), every author names each author who wrote before it, every `ASK <Author>:` line gets an `ANSWER <asker>:` (or a `CARRY` line in the closing section), a checker section exists and says how its method differs from the author's (`Independent method:`), and, with `--runs`, each section names a logged run id. | `python3 tools/thread_lint.py thread.md --runs runs.jsonl` |
| `run_log.py` | Keeps a JSON-lines log of agent runs (role, kind, task, tool-call cap, status, verdict) and fails on runs left running over 24 h, unknown statuses or verdicts; warns on runs over their cap or finished without a verdict. | `python3 tools/run_log.py --log runs.jsonl add --role Reviewer --kind checker --task "check one claim"` |
| `date_lint.py` | No event time in a record lies in the future (hand-typed times drift). By default it reads the first cell of each table row; `--pattern` picks another field. | `python3 tools/date_lint.py log.md` |
| `gap_check.py` | Every capability on a checklist has evidence on disk or an open row in a gap register, and every gap marked `add` gets a plan task within a set number of days. | `python3 tools/gap_check.py --checklist checklist.csv --register gaps.csv --plan plan.md` |
| `doc_lint.py` | Generic document checks: a word budget outside code fences (`words`), every money or percentage line carries a source marker (`marked`), and numbered table ids are unique, contiguous and resolve (`ids`). | `python3 tools/doc_lint.py marked strategy.md --marker "SOURCE NEEDED"` |
| `stale_number_check.py` | A number that was corrected is not cited again; a CSV of `pattern,now,reason` rules lists the old claims. | `python3 tools/stale_number_check.py notes/ --rules superseded.csv` |

Tests: `tests/test_tools_kit.py` (synthetic fixtures written in the test).
