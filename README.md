# rizz

**Submit a goal in plain English. Receive merge-ready PRs.**

`rizz` is a Claude Code plugin that auto-decomposes a coding goal into a topology of parallel git branches, runs coding agents in isolated worktrees, gates the resulting branches with a diff-reviewing joiner, and (optionally) opens a pull request for each branch worth keeping.

The pitch line:

> Conductor: *"You design the work. We run the agents."*
> Evo: *"You name the metric. We optimize against it."*
> **rizz: *"You name the goal. We ship gated PRs."***

---

## Install

`rizz` is two things: a Claude Code plugin (the user-facing surface) and a Python package (the engine the plugin shells out to). Install both:

```bash
# 1. Install the Python engine.
pip install rizz-engine            # or `pip install -e .` from a clone

# 2. Add the rizz marketplace and install the plugin.
/plugin marketplace add OmkarRayAI/rizz
/plugin install rizz@rizz
```

Once installed, two slash commands are available in Claude Code:

| Command | What it does |
|---|---|
| `/rizz:ship "<goal>"` | Auto-decompose the goal, run coding agents on parallel branches, open PRs |
| `/rizz:plan "<goal>"` | Dry-run: show the workspace topology MetaPlanner would emit, without spending tokens or touching disk |

---

## What you get

For a one-track goal: a single branch, a single PR, a joiner verdict on the run record.

For a multi-track goal (the interesting case): N branches running in parallel, N PRs each with its own diff, plus a joiner-emitted `merge_plan` that names which branches to keep, archive, or merge in what order. The PR body cross-references a run id you can audit later via `rizz status <run-id>`.

Example (`/rizz:ship "Add /login route returning 401 on bad creds, with tests and docs"`):
- MetaPlanner emits `WORKSPACE_TOPOLOGY: wsA: 1, 2 / wsB: 3 / default: wsA`
- Two worktrees allocated; impl + tests run in `wsA`, docs run in `wsB` — in parallel
- Conflict detection (`git merge-tree`) runs at joiner time
- Joiner emits per-branch verdicts and a structured merge plan
- Two PRs open against `main` with verdicts in their bodies

---

## When to use rizz vs. neighbors

| | rizz | Conductor.build | Evo |
|---|---|---|---|
| **Goal type** | Plain English ("add /login") | Anything (you decide topology) | Numeric ("make it faster") |
| **Decomposition** | Auto (MetaPlanner picks workspaces) | Human picks workspaces | Auto (tree search over a metric) |
| **Output** | Branches + PRs | Branches | Branches + scores |
| **When to pick** | Goal that splits across files/services | You want to drive it yourself | A benchmark exists |

If you're optimizing a function against a benchmark, use Evo. If you want to design the parallel work yourself, use Conductor. If you want the engine to figure out the parallel structure and ship PRs against it — that's rizz.

---

## Honest scope

Today the per-branch verdict is the joiner LLM reasoning over the diff and a conflict report. It is **not** the same as automated test runs against the goal — that harder gate (acceptance criteria + verifier agent + test-runner integration) is Phase 5 work, not yet shipped. So rizz today *recommends* which branches to keep; it does not yet *prove* they pass tests. Reviewers should still glance at the diff. The slogan is "you don't have to read every diff" — not "you can stop reading diffs forever."

---

## Power-user / Python API

The plugin is the recommended entry point, but the Python package is fully usable on its own:

```python
from rizz import Rizz, ClaudeCodeAgent

llm = ...   # your LangChain chat model
engine = Rizz(llm=llm)
answer, _, thinking = await engine.run(
    question="Add /login route returning 401 on bad creds, with tests and docs.",
    purpose="...",
    instructions="...",
    tools=[...],
    repo="/path/to/repo",
    open_pr=True,
    multi_workspace=True,
)
```

See [`rizz/README.md`](rizz/README.md) for full library docs and [`examples/`](examples/) for runnable smoke tests covering each phase of the engine (workspaces, coding agents, multi-workspace orchestration, conflict detection, persistence, CLI).

---

## CLI (used internally by the plugin, also available standalone)

```bash
rizz run    --repo PATH --question "..." --tools-config tools.py [...]
rizz list   --repo PATH                   # all runs
rizz status RUN_ID                        # one run, full detail
rizz prs    RUN_ID                        # PR URLs only, scriptable
rizz clean  --repo PATH --keep-recent 10
```

Run `rizz --help` for the full command tree. The `bin/rizz` plugin wrapper just delegates to `python -m rizz`, so anywhere you can run one, you can run the other.

---

## Roadmap

- ✅ Phase 1: Workspace primitive (git worktree + branch + notes)
- ✅ Phase 2: Coding agents as DAG leaves with structured `AgentResult`
- ✅ Phase 3: Topology-aware MetaPlanner + diff-reviewing joiner + auto-PR
- ✅ Phase 4: Persistence + CLI
- ⏭️ **Phase 5**: Verifier agent + acceptance criteria + true self-gating
- ⏭️ Phase 6: Multi-host plugin (Codex, Cursor, …)

The Phase 5 work turns the slogan from directional ("we recommend kept branches") to literal ("we prove kept branches pass acceptance criteria you can audit").

---

## License

MIT. See [`LICENSE`](LICENSE) (when added).
