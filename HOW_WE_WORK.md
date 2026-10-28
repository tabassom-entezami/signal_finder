# How we work, shown on real material

This page shows the working method on real records instead of describing it again. The process is explained in
[AGENT_TEAM.md](AGENT_TEAM.md), the safety layers (including the sandbox) in
[BUILT_WITH_CLAUDE.md](BUILT_WITH_CLAUDE.md) and the data checks in [ARCHITECTURE.md](ARCHITECTURE.md) section 3.

What was removed: role numbers, run ids, decision ids, file names, paths and dates. Roles are named by their
title. The discussion is shortened; the claims, the replies and the checker's findings are as they were written.
Research results, strategy, limits and money stay private.

What this page is not: proof that the method works. It shows the controls and one real record of them in use.
A checklist, a checker or a register existing does not show that the system is reliable, that the controls cover
every important failure, or that the signals make money; the signal results are in [RESULTS.md](RESULTS.md).
Where something below is a rule or an aim rather than a measured result, it says so.

## 1. A real discussion: should some agents merge into one?

**The question (founder):** "Is it better to mix some agents together into one agent? e.g. CTO can also be senior
software engineer and QA test engineer."

**Facts gathered before any opinion (main session, measured).**
- 50 agent files, all generated from one role-definition file. Merging means editing that file and regenerating,
  never editing an agent file by hand.
- All 50 descriptions are loaded into every turn: a recurring token cost and a source of routing errors.
- Nine roles are dormant until a later stage, but the generator still writes their files.
- Constraints written down first: (a) the author of a piece of work is never its checker; (b) research roles and
  builder roles get different rules, budgets and tools (only builders can edit files), so a merge across that
  line inherits contradictions; (c) zero use is not a reason to delete a role that waits for a later stage;
  (d) other tools route work by role, so role references must stay stable.

### Round 1: opinions

**Team Manager.** *Pick B* (merge only within the same rule block, keep each old role as "absorbed into" its
host). About 50 → 34 files. Rule: merge only pairs that never check each other's work and sit in the same rule
block. Merges: Legal &
Compliance → Legal Adviser, Finance → Finance Researcher, Security Threat Researcher → OPSEC, Competitor → Product
Researcher, and three more. *Not merged:* CTO + builders + QA, the founder's example: "One agent would write the
code, test it and approve it." *Risk:* routes still point to absorbed roles and go wrong without an error; fix:
remap in the same change, plus a check that fails on any route to an absorbed role. *Would change my answer:* a
material measured token cost; a merged agent's first-time acceptance rate drops; a merged pair has a record of
useful disagreements.

**CTO / Architect.** *Pick B.* The founder's example answered directly: the CTO can absorb the System Architect
(both design, neither edits code), "but not Backend or QA": one agent would design, build and verify its own
code. Proposes Backend Engineer → Quant Developer (both builders). Size: about 15 lines in the generator, about 60
in the role file; 50 → 36 files. *Risk:* stale generated files stay callable; check: a test that the agent files
equal the generated set. *Would change my answer:* a merged agent runs over its budget on mixed tasks; design
records the CTO writes lose their independent review.

**Agent Quality Auditor.** *Pick B*, about 34 files. Also retire the generalist Researcher: its description
matches all the specialists, so it is "the biggest source of routing errors". A defect found on the way, valid
under any option: generated descriptions stop at the first colon of the mandate, so routing text is lost. *Risk:*
merged prompts drop the absorbed role's self-checks; mitigation: carry every self-check line over word for word
and re-audit the first output of each merged agent.

### Round 2: replies by name

**Agent Quality Auditor → Team Manager and CTO.** Merge the System Architect into the CTO, using the CTO's own
fallback: a builder or the Report Analyst reviews every design record the CTO writes, so author ≠ checker still holds. On the
literature roles: "I concede to the Team Manager." On the two threat researchers: agree, and split them again if a
recorded disagreement between them turns up.

**Team Manager → CTO and Auditor.** System Architect into CTO: "agree, with one condition": my objection was the
CTO approving its own designs, and the CTO's fix closes it. I withdraw merging the Regulatory Researcher:
regulation and tax law come from different primary sources. Auditor, I reject splitting the Tax & Legal
Researcher: the Tax & Personal Finance role checks that research, so the split would merge author and checker.

**CTO → both.** Backend → Quant Developer dropped for a merged option: mark Backend dormant until its build part
starts. On the tax and legal researcher I take the Auditor's split. Agree to retire the generalist Researcher, and
the description fix ships under every option.

### Checker: Report Analyst (wrote nothing in the rounds)

Method: recomputed each claim from the raw files with its own scripts (regenerated the agents into a scratch
folder and compared, parsed the run log, recounted), instead of re-reading the opinions.

| Claim | Recomputed | Verdict |
|---|---|---|
| 50 files, all generated, none hand-edited | Regenerated into a scratch folder: identical | confirmed |
| Usage counts and the "never used" list | The raw count mixed role mentions with unrelated section references; 7 roles on the "never used" list had been run under their names | wrong |
| Dormant roles still get files | The generator only adds a "dormant" line, it does not skip them | confirmed |
| Descriptions cut at the first colon | 17 of 50 descriptions end with ":" | confirmed |
| Team Manager: 34 files after the merges | Two of the merges touch only dormant roles, which save nothing: 35 | wrong |
| CTO: 36 files | 36 | confirmed |
| Size of the routing remap | 11 table rows + 3 hook entries = 14 edits | measured |

Effect: neither error overturns B, but two inputs change. "Never used" is not a reason to merge (the overlap is),
and the converged set from round 2 gives 33 files.

### Outcome

The founder approved B: 33 generated files. Open points were settled by the usage data, not by more debate:
Backend went into Quant Developer (it had been run, so it was not dormant), the literature roles merged into the
one that had been used, and the Tax & Legal Researcher stayed whole (the Team Manager's point: a split makes the
tax role check its own research). The build in turn corrected one of the checker's own claims: a word it had
called a routing trigger was an agent name.

**What to notice.**
- Each role states its own choice before the replies round, so every disagreement is on record.
- Every concession names who it answers and why.
- The checker caught two wrong numbers: one in the facts gathered first, one in an author's count. The build
  then corrected the checker once: checking runs both ways.
- The founder's own example got a direct "no" with the reason, not a softened answer.
- The decision says what evidence would reverse it.

**What is measured and what is not.**
The file counts in the rounds (34, 35, 36) were the authors' estimates. The rest was measured after the build:

- **Agent set.** The old set was rebuilt from history: 50 files. The live set is 33. A test now runs the
  generator on a copy of the role files and fails if a stale agent file survives, if the routing hook names an
  agent without a file, or if the live set differs from the generated one. We broke it on purpose both ways to
  see it fail.
- **Token cost.** The agent list sent with every main-session request shrank from about 4,000 to about 2,700
  tokens (an estimate from its length). Against a typical request of about 300,000 input tokens, that saves under
  one token in two hundred. The token argument for the merge was real but small.
- **Outcomes before and after.** First-time acceptance went from 24 of 41 agent runs before the merge to 702 of
  799 after; for the same agents, 19 of 30 to 149 of 171. This is not evidence for the merge: on the same day the
  work moved from long research reports to short, capped opinion and checker runs, and the prompts were
  revised, so the rise cannot be credited to fewer agents.
- **Routing errors.** After the merge, no run went to a removed agent; three runs were sent to the wrong role
  and re-run. Before the merge such errors were not logged, so there is no comparison.
- **Defects caught by checkers.** Logged since the field was added: 139 defects on 75 checker runs. There is no
  before value.
- **Not measured.** The same tasks replayed with 50 and with 33 agents. With the token side known to be small,
  it is worth running only if the question reopens. The merge stays a judgement on overlap and routing, not a
  measured win.

## 2. Findings, gaps and priorities

- **One findings log.** Anything found during work (a bug, a wrong number, a broken assumption, a blocker) gets a
  row at once: what was found, the evidence, the owner, the plan and the status. Open rows stay at the top; a
  solved row moves to a Closed section at the end with its final status and is never deleted. Rows from the
  earlier rule, which deleted solved rows, were recovered from the saved file history into that section; rows
  added and removed between two saves left no record.
- **One task board, ranked.** Every open task has a priority and one owner role. P1 is work on the results and the
  forward record, P2 protects those results, P3 is the rest. Items only the founder can do (git, approvals,
  settings) have the founder as owner. A finding gets a priority when it moves onto the board.
- **Gaps are searched for, not waited for.** Each round the team looks for what is missing. The aim is that the
  founder never has to add an item; when the founder does, that counts as a miss. Every plan is run through a
  fixed gap checklist (time, money, security, blast radius, production safety, operations, simplicity) before it
  is reviewed. The checklist catches gaps of the kinds it names; it does not prove that no gap was missed.
- **Logs feed one records store, and it refuses rather than guesses.** The findings, decisions, agent runs, gaps
  and pre-registrations stay plain files; a sync copies them into a database and refuses any row it cannot read
  cleanly: a table row whose cells do not line up, a personal handle or wallet in the text, or a
  pre-registration whose frozen hash is missing. Every run counts the refused rows, and each one is traced to a
  cause. Some refusals are mistakes in the file and get fixed. Others are the guard working as meant, such as a
  person's handle kept out or a draft not yet frozen, and those stay refused.
- **Every agent run is logged and closed.** Each run gets a row when it starts and is closed with its tool calls,
  verdict and, for a checker, how many errors it caught. A run still open well after its work landed fails the
  after-round check until the manager closes it.
- **Promised versus built.** A check compares what the project says exists with what is on disk and working, and
  fails when a component is marked working but its data is stale.
- **Every report ends the same way:** *Blocked on me* (only what needs the founder), *Changed*, *Found*.

## 3. The risk register

Risks to money, accounts, time, legal standing and personal safety are kept in one register, owned by the Risk
Researcher and reviewed by the Team Manager. Each row has:

| Column | Meaning |
|---|---|
| Risk | What can go wrong, in one line |
| Stage | Which stage of the project it applies to |
| Likelihood | Per year, with a range when the rate is assumed rather than measured |
| Impact | Expected yearly loss, or "ruin" for losses that cannot be recovered |
| Automated check | The code that watches for it, or "none" written out plainly |
| Owner | The role (or the founder) who acts on it |
| Residual | What is left after the controls |

Ranking: risks that could end the project or harm the founder come first and are ranked by severity; the rest are
ranked by expected loss. Every row has a review date and an early warning that brings the review forward. Risks no
area owner saw get their own section, and the register is refreshed after major results. The values stay private.

An example row, made up to show the shape (the real rows and values are private):

| Risk | Stage | Likelihood | Impact | Automated check | Owner | Residual |
|---|---|---|---|---|---|---|
| A trading key leaks and is used to move money | Live trading | Low, assumed range (no measured rate) | Ruin | Keys have no withdrawal right; a check fails if a key file is readable by agents | Security Engineer | Trades can still be placed until the key is revoked |

## 4. How we make sure about data

In short (details in [ARCHITECTURE.md](ARCHITECTURE.md) section 3):
- Every dataset has a row in a registry with its licence, whether it is point-in-time safe, its owner and its
  refresh cadence. A dataset without a row is not used, and one that is not point-in-time safe never enters a
  backtest.
- The rule is that every number in a report is recomputed from the raw data by a checker who did not produce
  it, with a different method. This catches arithmetic, query and copying errors. It does not catch errors the
  author and checker share: the same wrong source data, the same wrong definition, or the same assumption.
- Held-out test data opens only through a loader that first verifies the hash of the frozen rules file.
