---
name: ship
description: |
  Auto-decompose a goal into parallel branches, run coding agents in
  isolated git worktrees, gate the resulting branches, and open
  pull requests for the kept ones. Use when the user wants the engine
  to *ship* code as PRs from a plain-English goal — not when they
  want to optimize against a metric (use evo:optimize for that) or
  drive workspaces by hand (use Conductor for that).

  TRIGGER when:
  - User says "ship", "open PRs", "fan out", "parallel branches"
  - User describes a goal that splits naturally across files/services
    (compliance update across N microservices, schema migration
    cascading through ETL jobs, broker SDK regeneration)
  - User asks for code changes they expect to *review and merge*

  SKIP when:
  - The goal is "make X faster / smaller / better-scored" — Evo's job.
  - The user wants to design the workspace topology themselves —
    Conductor's job.
  - The task is single-shot research or chat ("what does this code do?").

allowed-tools: Bash, Read, Edit, Write
---

# rizz:ship

The wedge: **submit a goal, receive merge-ready PRs**. The engine's
MetaPlanner picks the workspace topology, coding agents run in
isolated git worktrees, the joiner emits a per-branch verdict and a
structured merge plan, and (when `--open-pr` is set) `gh pr create`
opens a PR for each kept branch.

## What the user gets

For a one-track goal: a single branch, a single PR, a verifier
verdict in the run record.

For a multi-track goal (the interesting case): N branches, N PRs,
each with its own diff and its own verdict, plus a joiner-emitted
`merge_plan` that names the keep / archive / merge_order set. The
PR body cross-references the run id so the user can audit the
decision via `rizz status <run-id>`.

## Honest scope (read this before pitching it)

Today the verdict is the joiner LLM reasoning over diffs and a
conflict report. It is not the same as automated test runs against
the goal. That harder gate (acceptance criteria + verifier agent +
test-runner integration) is Phase 5 work; it is not in the engine
yet. So: the engine *recommends* which branches to keep — it does
not yet *prove* they pass tests. Reviewers should still glance at
the diff. The slogan is "you don't have to read every diff",
not "you can stop reading diffs forever."

## How to invoke

The skill drives the project's CLI. Two paths:

### Path A — invoke the CLI directly (recommended)

```bash
rizz run \
  --repo <PATH_TO_REPO> \
  --question "<goal in plain english>" \
  --tools-config <PATH_TO_TOOLS_CONFIG.py> \
  --open-pr \
  --cleanup never
```

`--cleanup never` is the right default for first-time use: it keeps
the worktrees and branches around so `rizz status
<run-id>` shows the full picture afterward. Switch to `on_success`
once you trust the runs.

`--tools-config` is a Python file that exposes `get_llm()` and
`get_tools()` callables. The `get_tools()` list should include any
`ClaudeCodeAgent` instances (or other `CodingAgent` subclasses) you
want available to the planner. See `examples/agent_smoke.py` for the
agent registration shape.

### Path B — use the Python API

```python
from pathlib import Path
from rizz import Rizz, ClaudeCodeAgent

llm = ...  # your LangChain chat model
impl = ClaudeCodeAgent("impl_agent", "Implements features")
tests = ClaudeCodeAgent("test_agent", "Adds tests")
docs = ClaudeCodeAgent("docs_agent", "Updates docs")

engine = Rizz(llm=llm)
answer, _, thinking = await engine.run(
    question="Add /login route returning 401 on bad creds, with tests and docs.",
    purpose="...",
    instructions="...",
    tools=[
        {"class": "ClaudeCodeAgent", "name": "impl_agent", ...},
        {"class": "ClaudeCodeAgent", "name": "test_agent", ...},
        {"class": "ClaudeCodeAgent", "name": "docs_agent", ...},
    ],
    repo=Path("/path/to/repo"),
    open_pr=True,
    multi_workspace=True,    # let MetaPlanner decide topology
)
```

## Workflow this skill follows

1. Read the user's goal. Quote it back in one line. Confirm scope
   before spending tokens (if the goal is ambiguous, ask 1 clarifying
   question; do not loop).
2. Locate the target repo and the `--tools-config` file. If the user
   hasn't pointed at a tools config, ask for it — coding agents
   require this and silent fallback would be confusing.
3. Run `rizz run` with the flags above. Stream output
   as the engine reports topology, conflict detection, and merge plan.
4. After completion, run `rizz status <run-id>` to
   show the user:
   - Which workspace groups MetaPlanner created
   - Which branches survived the joiner verdict
   - Which PRs were opened (with URLs)
   - Which branches were archived (and why)
5. If verdicts include `needs_replan` or any branch is archived,
   surface the joiner's reasoning verbatim — don't paraphrase. The
   reviewer needs the engine's own words to trust them.

## What NOT to do

- Don't claim the engine "verified" the changes. It reviewed them.
  Verification is Phase 5 work — see `transient-discovering-hoare.md`.
- Don't auto-approve PRs or run `gh pr merge`. The whole point is
  the human reviews the *kept* set; auto-merging defeats the wedge.
- Don't skip `--cleanup never` on the first run for a new user.
  Worktrees they can't inspect are not workspaces — they're black
  boxes.
- Don't run this on a repo with uncommitted changes the user cares
  about. The engine works in worktrees so the host isn't disturbed,
  but verify by checking `git status` first.

## Comparison to neighbors (so the user picks the right tool)

- **Conductor.build** — human picks the topology, runs the agents,
  reviews diffs in a Mac app. Use Conductor when you want to drive
  the orchestration yourself.
- **Evo (`evo:optimize` / `evo:discover`)** — measures code against
  a benchmark and iteratively improves it. Use Evo when there's a
  number to maximize.
- **`rizz:ship`** (this skill) — auto-decomposes a goal into
  branches, gates them via the joiner, opens PRs. Use when there is
  no metric, just shippable work.

## Telemetry / persistence

Phase 4 wired a SQLite store at `<repo>/.rizz/state.db`. After a
run you can:

```bash
rizz list --repo <REPO>          # all runs
rizz status <RUN_ID>             # one run, full detail
rizz prs <RUN_ID>                # PR URLs only, scriptable
rizz clean --repo <REPO> --keep-last 10
```

These commands are how the user audits decisions later. Mention them
when the run completes.
