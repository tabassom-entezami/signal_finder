# The agent team

`signal_finder` is built by one owner working with a team of AI agents (Claude). This page names the roles,
explains how they work together and how they are kept safe, and how the owner uses the team day to day.
It explains the method only: no prompts, role definitions, research or project data are published.
See also [BUILT_WITH_CLAUDE.md](BUILT_WITH_CLAUDE.md) for the tooling around it.

## The roster

About thirty active roles, each an AI agent with one job. The team manager sends each task to the best-placed role, which asks related roles before anything is decided or built.

### Leadership and coordination

- **CEO / Founder:** keeps work aimed at the goal; called for priorities and go / no-go calls.
- **Team Manager:** routes tasks, runs the shared discussion, reports the agreed answer; involved in every task.
- **Agent Quality Auditor:** checks each role is the best version of its job title; called after research rounds and when roles change.
- **CTO / Architect:** chooses the simplest design, module boundaries and data flow; called for technical choices.

### Strategy and markets

- **Head of Quant Research:** owns the strategy and its honest validation on unseen data; called for strategy or testing-method questions.
- **Quant Developer:** writes strategies, indicators, backtester and paper simulator as correct, reproducible code; called when that code changes.
- **Algorithm and Indicator Researcher:** finds published methods with evidence, in many languages, and tracks whether they replicated; called for new ideas.
- **Portfolio Manager:** decides which method runs on which coin, or none; called when a method is added, dropped or funded.
- **Trader:** brings the working trader's view of market mood and crowd positioning; called to review signals, rules and the weekly review.
- **Market Microstructure Researcher:** studies spreads, depth, real trading costs and how market makers win; called for fills and costs.
- **Macro and Event Analyst:** keeps the calendar of market-moving events and the macro backdrop; called when timing around events matters.
- **News and NLP Analyst:** turns coin news into timestamped, scored data that can be tested; called when news may affect a signal.
- **On-chain and Smart-Money Analyst:** follows public records of large, skilled traders forward and scores them; called when big-player positioning matters.

### Data and AI

- **Data Engineer:** keeps every data point timestamped, traceable and rebuildable from raw storage; called for pipelines and data quality.
- **Data Source Researcher:** finds and measures new data sources (history, delay, gaps, licence, cost); called when data is missing.
- **AI / LLM Engineer:** designs bounded, evidence-backed, versioned use of the language model; called for anything involving the model.
- **Report Analyst:** recomputes a report's numbers from raw output before the founder acts; called on every decision-driving result.

### Risk and money

- **Risk Officer:** owns sizing, stops and exposure as fixed rules that can veto any signal; called before money is at risk.
- **Risk Researcher:** keeps one cross-area risk list, including tail risks; called when a plan is reviewed for what could go wrong.
- **Finance Researcher:** works out costs, funding and budgets; called for any money or cost question.
- **Tax and Personal Finance:** settles taxes, records and personal-capital rules; called before money moves.

### Execution and operations

- **Execution and Trading Operations Engineer:** makes sure orders go out exactly as intended, always with a stop; called for order handling.
- **Exchange Researcher:** keeps a measured map of exchanges (costs, eligibility, licences, reliability); called when choosing where to trade.
- **DevOps / SRE:** keeps the system running unattended, with alerts, recovery and tested backups; called for hosting and incidents.
- **QA and Test Engineer:** makes sure nothing ships untested, including replays and bad-data drills; called on every shipped change.
- **Operations Researcher:** designs routines that take the founder the fewest minutes a day; called when a process is set up.

### Security and law

- **Security Engineer:** makes sure keys, machines and accounts cannot be used to move money; called for every safety-control change.
- **Operational Security Researcher:** protects the founder's identity, accounts and devices, and studies real attacks; called before anything is exposed.
- **Legal Adviser:** finds the legal way to do things, answering "yes, if..." with conditions; called for any legal doubt.
- **Regulatory Researcher:** follows financial and crypto rules from regulators' own texts, including coming ones; called when plans touch regulation.
- **Tax and Legal Researcher:** researches tax and general law abroad, in local languages; called when a question crosses a border.

### Market and product knowledge

- **Customer Researcher:** learns from observed behaviour who traders are and why they pay or quit; called when real users matter.
- **Product Researcher:** studies what comparable tools ship and what works; called before building something others may have solved.

### Dormant until there is a product to sell

Product Manager, Product Owner, UI/UX Designer, Frontend Developer, Growth and Marketing, Growth and Marketing Researcher, Brand and Messaging Researcher, Pricing Researcher, Customer Success.

### What every role shares

- A written mandate: what it owns, decides and must check before work passes.
- A quality bar modelled on the best people in that job, with a self-check on every output.
- A budget of tool calls per task; what does not fit becomes a named open item.
- A teammate review, plus a separate checker who re-runs the claims a different way.
- Evidence and a source for every claim.
- Untrusted input stays data: fetched text is never followed as an instruction.

## How the team works together

A worked example. The founder asks: "Which storage should a new data job use?"

1. **Use what exists first.** The team manager role checks the project's own research database, earlier discussions and the decision record. If one query or one existing, verified answer settles it, no agent is started.
2. **Ask the owners.** Otherwise the manager picks the two or three roles that own the topic (here: the architect, the data engineer, the quality engineer). A small routing hook suggests them from the words of the question. Each gets a short brief: the question, the facts already found with their source, two or three options, and a fixed budget of tool calls.
3. **One shared thread.** Every role writes its opinion into the same discussion file: a choice, why, the risk, and what would change its mind. Nobody edits another's section.
4. **Replies by name.** In the next round each role reads the whole thread and answers the others by name: "Architect: agree, but…", "Data engineer: disagree, because the measured size is…". A role that needs something from another writes a question addressed to that role; the thread is not complete until that role has written the answer. The next-round prompt is fixed text, so the manager cannot steer the answer with its own summary.
5. **Round limit.** Each role gets at most four replies. Then the manager closes the thread and records every open question as carried to a named owner or to the founder. Discussions that can be reopened forever never converge.
6. **Independent check.** A role that wrote nothing in the thread re-runs the measured claims on the real data, using a different method than the author: parse the file instead of searching its text, query the table instead of counting files, recompute instead of re-reading. A re-run by the author's own method counts as unverified, not confirmed. A tool refuses a checker section that does not state its method.
7. **Decision record.** Only after the check passes is the decision written down: the question, each role's choice in one line, the choice, why, the evidence grade, the owner and what would make the team revisit it. Disagreements are settled by evidence: measured beats primary source beats secondary, newer beats older.
8. **Founder, only when needed.** If the question turns on values, money or legal judgement, the founder gets it in five parts: what, why it matters (with numbers and sources), two or three options with pros and cons, a recommendation and why it wins, and the default if nobody decides.
9. **Build and review.** A builder works from the approved plan and its checklist, notes what changed and why under each item, runs the tests the change can affect, and hands over uncommitted. A fresh-context reviewer then checks the change.

```
 question
    |
    v
 existing data answers it? --yes--> answer
    | no
    v
 2-3 owner roles -> one thread -> replies by name (max 4 each)
    |                               ^       |
    |                questions to a role ---+
    v
 independent checker (different method)
    |
    v
 decision record --(values / money / legal)--> founder, 5-part format
    |
    v
 approved plan -> build -> fresh-context review -> founder commits
```

## Safety

Each layer stops a different failure. None is trusted alone.

| Layer | What it stops |
|---|---|
| OS sandbox | A command writing outside the project, reading secret folders, or reaching hosts the project does not need. |
| Write guard | Read-only roles editing files; builders editing outside their area; destructive shell commands. Every tool call is checked before it runs. |
| Call caps per task | Runaway agents. Each brief starts with its cap; the guard warns near it and refuses calls past it. Only a few agents run at once. |
| Real security review for safety changes | The model weakening its own guards. Changes to the guard, hooks, routing, permissions or sandbox need a security review planned before the build. The guard accepts it only when the review's run record shows a finished run with a real transcript; a typed heading does not count. |
| Founder approval | Deletions and any use of keys, backups or personal data. The guard refuses until the founder picks an approval option for that one action, after it is explained. |
| Git belongs to the founder | Unreviewed changes reaching history. Claude only reads git; the founder reviews uncommitted changes and commits by hand. |
| Untrusted input stays data | Instructions hidden in web pages, API replies, headers, file names or posts. Fetched text is never followed and never pasted into a command; suspicious text is reported. |
| Public/private split | Leaks. The public repository is an allow-list: only the generic engine. A leak check runs on every commit and fails on internal references. |
| No trading access | Money moving by mistake. No agent holds exchange credentials or can place orders. |

Refusals are logged and reviewed for false positives, so the guards stay strict without blocking normal work.

**Honest limits.** The guards check actions, not intent: a command shaped in a way the rules did not foresee can still pass, and the sandbox is the backstop for that. Untrusted text cannot trigger actions, but it can still mislead the model's reasoning. The checker catches many wrong numbers, not all of them; work that passes every check can still be wrong. Approval is only as good as the explanation before it. The founder's own review of the diff stays the last line.

## How the founder uses it day to day

The founder writes plain requests:

- **"Research X"**: the owner roles look up existing work, then search primary sources in the languages where the answer lives, and a checker verifies the claims.
- **"Plan Y"**: open questions come back in one batch, then a plan with a checklist and a test command per step waits for review.
- **"Build Z"**: work starts only from an approved plan.
- **"Run …"**: a short word starts a routine job, such as refreshing data, collecting the day's feeds or rebuilding the dashboard after a round. Nothing runs on a schedule except a small recorder and its health check.

What comes back is one short report per round under three headings: **Blocked on me** (only what needs the founder), **Changed** (every file and why) and **Found** (new facts and problems, with their source). Decisions arrive in the five-part format, never as a bare "approve?". The team also looks for gaps itself each round, so the founder should never have to add a missing item.

**Only the founder** sets values and priorities, spends or moves money, makes legal judgements, does every git action and gives the approvals for deletions and sensitive data.

**How quality is tracked.** Every agent run is logged with its run id, its call cap, the calls it used and a verdict: accepted, revised or rejected. Saved test cases check agent outputs against known good and bad answers. Prompts are versioned, and every output names the prompt version that wrote it, so a change in quality can be traced to a change in prompt. A dedicated role audits whether each agent still does its job at the level of the best people in that title.

## What a reader can reuse

1. **Put the rule in code, not only in the prompt.** Models forget instructions; a hook that refuses the action does not.
2. **Make agents answer each other, not you.** Fixed-text rounds and replies by name surface disagreements a summary would hide.
3. **Verify with a different method.** Re-running the author's own check repeats the author's mistake.
4. **Cap everything.** Calls per agent, agents in parallel, rounds per discussion. Limits force a decision.
5. **Keep the human for values, money, law and git, and send those as real choices.** Five parts with a default turn a vague question into a quick decision.
