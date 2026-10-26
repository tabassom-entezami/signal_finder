# How signal_finder is built with Claude Code

This page explains how the project is developed with Claude Code, Anthropic's coding
agent. It covers the working method and the safety layers. It does not include the
prompts, role definitions, internal tooling or any research results.

One person owns the project and makes every decision that involves values, money or
legal judgement. Claude does the research, planning, building and checking inside
fixed rules.

## 1. The layers

```
            founder
               │  request
               ▼
   ┌──────────────────────┐   reads at start   ┌────────────────────────┐
   │  Claude Code session │ ◄───────────────── │  instruction files     │
   └──────────────────────┘                    └────────────────────────┘
      │        │        │
      │        │        └── agents: separate helpers with their own context
      │        └─────────── skills: procedures loaded only when relevant
      └──────────────────── MCP tools: code navigation, memory, docs lookup
               │
               ▼
     hooks and guards check every tool call before it runs
```

- **Instruction files** (Claude Code's `CLAUDE.md` convention) state the rules: how to
  work, the stack, the commands, what must never happen. They load at the start of
  every session; personal preferences live in a separate, untracked layer that may add
  rules but never contradict the shared ones.
- **Skills** are packaged procedures (for example: plan a change, review a change,
  recompute a report from raw data). They load only when the task needs them, which
  keeps the context small.
- **Agents (subagents)** are separate Claude instances with a fresh context and a
  limited tool set. They work in parallel and return a summary.
- **Memory** is a set of plain Markdown notes outside the repo: working rules,
  per-project facts, lessons learned and fixed bugs. The rule is "current facts only":
  what is no longer true is deleted. Anything the project itself depends on lives in
  the repo, not only in memory.
- **Hooks and guards** are small programs Claude Code runs before and after tool
  calls. They enforce rules in code instead of trusting the model to remember them.

### The instruction files

| File | Shared | Loaded | Purpose |
|---|---|---|---|
| `CLAUDE.md` | yes | every session | Project rules, workflow, commands, stack, lessons learned |
| `CLAUDE.local.md` | no | every session | Personal additions; never contradicts the shared file |
| `AGENTS.md` | yes | by other coding tools | A generated copy of the rules for non-Claude agents |
| `CONTEXT.md` | yes | on demand, by skills | Shared facts (project profile, repos, tool routing), so skills point to one place instead of copying them |
| Definition of done | yes | when building or reviewing | The checks a change must pass: every caller found, data flow traced, tests mapped, blast radius stated |
| Path rules | yes | only when matching files change | Narrow rules (for example infrastructure) that would otherwise bloat the main file |
| The plan file | no | during work | Objective, checklist, test command per step, review notes; it survives context resets |

Why several files: the model's context is limited. Always-loaded files hold rules only, facts live in one
shared file, long procedures live in skills, and narrow rules load only when they apply.

### The discipline layer

[Superpowers](https://github.com/obra/superpowers) is a plugin of process skills that turn good engineering
habits into steps the model must follow. They trigger when the situation matches:

| Situation | What it forces |
|---|---|
| Starting anything new | Understand intent, requirements and design before any code |
| Requirements are clear | A step-by-step plan with exact files and checks |
| Writing code | A failing test first, then the code that makes it pass |
| Something breaks | The root cause with evidence before any fix |
| About to say "done" | Run the checks and show the output; no claim without proof |
| Work finished or reviewed | A fresh reviewer checks the change; each review comment is verified, not blindly accepted |
| Several independent tasks | Split across agents running in parallel |

### Skills, agents, code navigation and memory

- **Skills.** A skill is a folder with a description and a procedure (plus optional scripts). Only its name and
  one-line description stay in context; the full text loads when the task matches. There are process skills (how
  to work), task skills (plan, review, security review, recompute a report from raw data) and memory skills (save
  lessons, clean memory weekly, write a handoff). Rule of thumb: a procedure explained to the model twice becomes
  a skill.
- **Agents.** Read-only search agents, planning agents, fresh-context reviewers (they see the diff without the
  author's reasoning) and builders (only when asked). Here every agent is generated from one role-definition file
  plus a quality-standards file and shared rules: researchers get the research rules, builders get the build rules
  and the edit tool, managers may start other agents.
- **Code navigation by symbol.** [Serena](https://github.com/oraios/serena) runs a language server so the model
  can list a file's functions without reading it, jump to a definition and find every caller of a function. That
  caller census is required before any signature changes, and it finds callers a text search would miss.
- **Memory.** Plain Markdown notes in four groups: working rules and mistakes not to repeat, per-project facts and
  the current handoff, lessons by topic, and fixed bugs (symptom, cause, fix; searched before any debugging).
  Read at session start, written when something durable is learned, cleaned weekly.
- **Other tools, added only when a task needs them:** current library documentation instead of guessing, the git
  host for pull requests and CI results, and IDE diagnostics.
- **Right agent, right data, fresh data, checked by code.** Every task goes to the role named in a routing table,
  and every owner named in the data registry must be a real role. Data is used only after it has a registry row
  (licence, point-in-time safety, storage, owner), and a refresh job warns when it is behind. See
  [ARCHITECTURE.md](ARCHITECTURE.md) section 3.

## 2. The workflow

1. **Brainstorm:** for any non-trivial change, explore intent and design first and ask
   all open questions in one batch.
2. **Plan:** write a plan with a checklist, the files to touch and the command that
   proves each step works. The plan waits for the owner's review.
3. **Build:** work through the checklist and record what changed, where and why.
4. **Review:** a fresh-context reviewer checks the diff, plus a security review where
   keys, money or external input are involved.

Trivial work (a typo, one config value) skips the plan.

## 3. The agent team

Claude works as about thirty specialist roles. Each role has a written mandate, a
quality bar modelled on the best people in that job, and a budget. Agents are
generated from those definitions, not hand-written, so every role gets the same
shared rules.

The roles are grouped by area: leadership and coordination, strategy and markets, data and AI, risk and money,
execution and operations, security and law, and market and product knowledge. The full roster, with one line
per role, is in [AGENT_TEAM.md](AGENT_TEAM.md).

Researcher roles search primary sources in English and in the languages where the
answer actually lives, grade each source, and trace every number to its origin and
date.

### How a piece of work flows

```
 question ──► related roles write opinions into one shared thread
                    │
                    ▼
           roles read each other and reply by name
           (agree, disagree, or a merged proposal)
                    │
                    ▼
           an independent checker re-runs the claims on real data,
           using a different method than the author
                    │
                    ▼
           decision record (options, evidence, recommendation)
                    │
                    ▼
           build from an approved plan ──► fresh-context review
```

- A team manager role routes each task to the roles that own the topic.
- Nothing is answered "from the model's head": existing data and earlier work are
  checked first.
- A checker that repeats the author's own method does not count as confirmation.
- Questions only a human can settle (values, money, legal) go to the founder as a
  short decision: what it is, why it matters, two or three options with their trade-offs,
  a recommendation, and the default if nobody decides.
- Losses, failed tests and time-outs are reported as plainly as wins.

## 4. Safety layers

Several independent layers, so no single mistake can do harm:

1. **OS sandbox.** Every shell command runs in a sandbox that limits which folders can
   be written and which network hosts can be reached. Secret folders cannot be read.
   One host deny-list feeds both the sandbox (shell commands) and a policy check on the
   model's own web fetches and browser calls. The safety files
   themselves (guards, hooks, settings, the host list) cannot be written from a shell
   command at all: the founder changes them, and a model edit needs a security review
   plus the founder's approval of that one edit. A blocked command is reported, never
   worked around.
2. **Write guard.** A hook checks every tool call before it runs. Agents are mostly
   read-only; only builder roles may edit files, and only in their area. Changes to
   the safety controls themselves need a security review first.
3. **Call caps.** Every agent run has a fixed budget of tool calls and time; the guard
   refuses calls past it. A limited number of agents run in parallel.
4. **Founder approvals.** Deleting anything, or touching keys, backups or personal
   data, is refused until the founder explicitly approves that one action.
5. **Git belongs to the founder.** Claude only reads git. Commits, branches and pushes
   are done by hand after the founder reviews the uncommitted changes.
6. **Untrusted input stays data.** Text fetched from the web, APIs or files is never
   treated as instructions, whatever it claims to be.
7. **No trading access.** No agent holds exchange credentials or places orders.

Refused actions are logged, and the log is reviewed for false positives so the guards
stay strict without blocking legitimate work.

## Why work this way

A single model answering alone can be confident and wrong. Splitting the work into
roles that must cite evidence, read and challenge each other, and pass an independent
check catches many of those errors before they reach code. The guards make the most
important rules hold even when the model forgets them.

The research rules exist for a second reason: the people running a test are the easiest to fool. After a bad
result it is tempting to change a parameter, drop a coin, move the window or promote the best variant. So
every rule, constant and pass bar is written down and hashed before the test data is opened, the test runs
once, and every variant tried is counted as a trial. When the first strategy failed, the record said FAIL, and
that verdict could not be argued away afterwards.

Three rules keep that process honest without letting it grow past its purpose:

- **Every piece of work names its purpose.** It either tests a hypothesis (a counted trial or a pre-registered
  test) or adds a capability that something or someone uses (a gate, a job or a user, plus the command that
  shows it working). Work that only makes the architecture nicer is not built.
- **No shortcuts on evidence.** Collected data is kept, validated and learned from. Every number is checked
  against the data by someone who did not produce it, and nothing is guessed.
- **The search has an end.** The ten-week window for a new strategy family, fixed before the first test, is
  kept. Any later search needs a new, dated decision and ends on 31 March 2027 at the latest, or earlier if the
  Sharpe ratio a new rule would need to clear the deflated test rises above 2.
