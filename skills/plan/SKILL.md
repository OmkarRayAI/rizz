---
name: plan
description: |
  Run the MetaPlanner only — preview the workspace topology and the
  meta plan for a goal without spawning agents, allocating worktrees,
  or touching disk. Use when the user wants to *critique* the engine's
  decomposition before committing to a real run, or when comparing
  topologies for a goal that's expensive to execute.

  TRIGGER when:
  - User says "preview", "dry-run", "what would the engine do", "show
    me the topology", "plan only"
  - User wants to compare alternative phrasings of a goal (does the
    MetaPlanner emit different topologies for "fix X then test X" vs
    "fix X and test X in parallel"?)
  - User wants to inspect the MetaPlanner output for tuning
    (`query_understanding`, `temporal_context`, etc.)

  SKIP when:
  - The user wants to actually ship the code (use rizz:ship).
  - The goal is short and unambiguous; running the planner just adds
    latency without insight.

allowed-tools: Bash, Read, Edit, Write
---

# rizz:plan

Cheap dry-run. Hits only the MetaPlanner — never the Planner, never
the agents, never `git`. Returns:

1. The **meta plan text** the MetaPlanner emitted.
2. The **parsed topology** (groups, default group, task indices).
3. A note about whether the engine would run in single-workspace or
   multi-workspace mode, and why.

This is the right tool for "would the engine do this in parallel,
or sequentially?" before paying for a real run.

## How to invoke

There's no dedicated CLI subcommand for this yet — it's a one-shot
Python invocation against the engine's own pieces. Use the script
template below; tweak `question`, `purpose`, `instructions`, and the
tool list to match the user's intent.

```python
import asyncio
from rizz.metaplanner import MetaPlanner
from rizz.topology import parse_topology_block
# `llm` and `tools` are whatever the project normally uses; they need
# to exist because the MetaPlanner prompt template references them.

async def main():
    mp = MetaPlanner()
    result = await mp.retrieve_meta_data(
        question="<the user's goal>",
        purpose="<the same purpose rizz:ship would use>",
        instructions="<the same instructions>",
        tools=tools,           # list of StructuredTool instances
        llm=llm,
        message_manager=None,
    )
    meta_plan = result["meta_plan"]
    topology = parse_topology_block(meta_plan)
    print("=" * 60)
    print("META PLAN")
    print("=" * 60)
    print(meta_plan)
    print()
    print("=" * 60)
    print("TOPOLOGY")
    print("=" * 60)
    if topology.is_multi:
        print(f"Multi-workspace mode ({len(topology.groups)} groups)")
        for name, indices in topology.groups.items():
            marker = " (default)" if name == topology.default_group else ""
            print(f"  {name}{marker}: tasks {indices}")
    else:
        print("Single-workspace mode (no topology block emitted, or only one group)")

asyncio.run(main())
```

Save this as `scripts/preview_plan.py` (or wherever the project
already keeps scratch scripts) and run it with the project's tools
config.

## Reading the output

**If the topology is single-workspace:** the MetaPlanner judged the
goal as one cohesive track. `rizz:ship` will run it on one
branch. This is correct for ~80% of goals.

**If the topology is multi-workspace:** the MetaPlanner saw multiple
disjoint coding tracks. Each group gets its own branch. Look at the
group names and indices — does the partition actually make sense?
Common smell tests:

- **Too many groups.** If a 4-task plan has 4 groups, the MetaPlanner
  is over-eager. Each group becomes a PR; the user has to review N
  PRs that should have been one. Reword the goal.
- **One huge group + tiny ones.** Often a sign that the small groups
  are noise (e.g., a single docs update that should just be folded
  into the main implementation).
- **Indices that overlap (caught by the parser warnings).** Means
  the MetaPlanner mis-attributed tasks. Check the warnings printed
  by `parse_topology_block` — they'll log `"task N claimed by wsA
  and wsB — keeping first"`.

## Iterating on the goal

The MetaPlanner is sensitive to:

- **Goal phrasing.** "Add feature X, with tests, and document it" is
  more likely to multi-fan than "Add feature X" alone.
- **The `instructions` text.** If `instructions` says "always
  separate impl from docs", the MetaPlanner takes that as a hint to
  emit a topology block. If it says "keep changes minimal", the
  opposite.
- **The available tools.** A tool list with three coding agents
  reads as "we're doing code work"; one with retrieval tools reads
  as "we're doing research."

Tweak one variable at a time and re-run `rizz:plan` to see how
the topology changes. This is also the right way to debug "why did
the engine pick a single workspace when I wanted parallel branches"
without spending a real run on it.

## What this skill does NOT do

- Does not allocate worktrees. Does not run agents. Does not touch
  the host repo. Does not write to the run store.
- Does not validate that the topology *will work* — only that the
  MetaPlanner emitted it. Real conflict detection happens later, at
  joiner time, during a `rizz:ship` run.
- Does not produce a number / score / benchmark. If you want
  measured comparison between approaches, that's `evo:optimize`.

## When to escalate to rizz:ship

When the topology looks right and the user is ready to commit
real LLM tokens + worktree disk + (maybe) `gh pr create` calls.

The plan and the ship invocation should use the *same* `question`,
`purpose`, `instructions`, and `tools` — that's the only way the
preview is faithful to the real run.
