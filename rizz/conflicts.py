"""Phase 3 — pairwise conflict detection between workspace branches.

Wraps `git_utils.merge_tree` to flag pairs of branches that would
conflict if merged. The result is rendered into the joiner's
scratchpad so the LLM can decide whether to Replan.

We detect; we do not resolve. Cost is O(n²) where n = #workspaces;
typical n=2-5 keeps this trivial. Larger fan-outs would want a
topo-sorted n-way merge — out of scope for Phase 3.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from itertools import combinations
from pathlib import Path
from typing import List

from . import git_utils

log = logging.getLogger(__name__)


@dataclass
class ConflictPair:
    branch_a: str
    branch_b: str
    files: List[str] = field(default_factory=list)


async def detect_conflicts(
    repo: Path, branches: List[str]
) -> List[ConflictPair]:
    """Pairwise conflict detection. Returns one ConflictPair per
    conflicting pair; clean pairs are omitted from the result.

    Bare-string branches must be valid refs in `repo`. All
    Phase 1 worktrees fork from the same `head_sha(repo)`, so a
    common ancestor always exists and `merge-tree` runs cleanly.

    On `merge-tree` errors (e.g. older git lacking `--write-tree`),
    we log and return an empty list rather than failing the run.
    """
    if len(branches) < 2:
        return []
    pairs: List[ConflictPair] = []
    for a, b in combinations(branches, 2):
        try:
            clean, files = await git_utils.merge_tree(repo, a, b)
        except git_utils.GitError as e:
            log.warning("merge-tree failed for %s vs %s: %s", a, b, e)
            continue
        if not clean:
            pairs.append(ConflictPair(branch_a=a, branch_b=b, files=files))
    return pairs


def render_conflict_report(pairs: List[ConflictPair]) -> str:
    """Render a `CONFLICTS:` block for injection into the joiner
    scratchpad. Empty pair list renders as a benign no-conflicts line.
    """
    if not pairs:
        return "No cross-branch conflicts detected."
    out = ["CONFLICTS:"]
    for p in pairs:
        flist = ", ".join(p.files) if p.files else "(no file list reported)"
        out.append(f"  {p.branch_a} vs {p.branch_b}: {flist}")
    return "\n".join(out)
