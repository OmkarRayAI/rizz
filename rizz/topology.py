"""Phase 3 — workspace topology parsing.

A `Topology` captures the named groups MetaPlanner emitted (e.g. `wsA`,
`wsB`) and the task indices each owns. An empty `Topology` means
single-workspace fallback — Phase 1/2 path verbatim. The block grammar:

    WORKSPACE_TOPOLOGY:
      wsA: 1, 2
      wsB: tasks 3-4
      default: wsA

Block ends at the first blank line or EOF. Group names match
`[A-Za-z_][A-Za-z0-9_]{0,31}`. Range syntax (`1-3`) is expanded.
`default:` is optional; first declared group wins if absent or unknown.

This module never raises on malformed input — it logs warnings and
falls back to an empty Topology. The engine then reverts to single-ws
mode without disrupting the run.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

log = logging.getLogger(__name__)

# Block delimiter (case-insensitive). We use a distinct sentinel so a
# meta plan that *mentions* "workspace topology" in prose doesn't
# accidentally start the block.
_TOPOLOGY_BLOCK_RE = re.compile(
    r"^[ \t]*WORKSPACE_TOPOLOGY:[ \t]*$(?P<body>.*?)(?:^[ \t]*$|\Z)",
    re.MULTILINE | re.DOTALL | re.IGNORECASE,
)
# `wsA: 1, 2, 4` or `wsA: tasks 1-3, 5`
_GROUP_LINE_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z_][A-Za-z0-9_]{0,31})\s*:\s*"
    r"(?:tasks?\s+)?(?P<list>[0-9,\s\-]+?)\s*(?:#.*)?$"
)
_DEFAULT_LINE_RE = re.compile(
    r"^\s*default\s*:\s*(?P<name>[A-Za-z_][A-Za-z0-9_]{0,31})\s*(?:#.*)?$",
    re.IGNORECASE,
)


@dataclass
class Topology:
    groups: Dict[str, List[int]] = field(default_factory=dict)
    default_group: Optional[str] = None

    @property
    def is_multi(self) -> bool:
        return len(self.groups) >= 2

    def for_task(self, idx: int) -> Optional[str]:
        for name, indices in self.groups.items():
            if idx in indices:
                return name
        return self.default_group


def parse_topology_block(meta_plan_text: Optional[str]) -> Topology:
    """Extract the WORKSPACE_TOPOLOGY block from MetaPlanner output.

    Returns an empty `Topology` when the block is absent, malformed, or
    the input is None/empty. Never raises.
    """
    if not meta_plan_text:
        return Topology()

    m = _TOPOLOGY_BLOCK_RE.search(meta_plan_text)
    if not m:
        return Topology()

    body = m.group("body")
    groups: Dict[str, List[int]] = {}
    default_group: Optional[str] = None

    for raw in body.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        dm = _DEFAULT_LINE_RE.match(line)
        if dm:
            default_group = dm.group("name")
            continue

        gm = _GROUP_LINE_RE.match(line)
        if not gm:
            log.warning("topology: unrecognized line %r — ignoring", raw)
            continue

        name = gm.group("name")
        indices = _expand_index_list(gm.group("list"))
        if not indices:
            log.warning("topology: group %r has no indices — ignoring", name)
            continue
        if name in groups:
            log.warning("topology: group %r redeclared — keeping first", name)
            continue
        groups[name] = indices

    if not groups:
        return Topology()

    # default_group sanity: must be a known group; else first declared.
    if default_group is None or default_group not in groups:
        if default_group is not None:
            log.warning(
                "topology: default %r not declared; falling back to %r",
                default_group,
                next(iter(groups)),
            )
        default_group = next(iter(groups))

    # Cross-check: each task index appears in at most one group.
    seen: Dict[int, str] = {}
    cleaned: Dict[str, List[int]] = {}
    for name, indices in groups.items():
        kept: List[int] = []
        for i in indices:
            if i in seen:
                log.warning(
                    "topology: task %d claimed by %r and %r — keeping first",
                    i,
                    seen[i],
                    name,
                )
            else:
                seen[i] = name
                kept.append(i)
        cleaned[name] = kept

    return Topology(groups=cleaned, default_group=default_group)


def _expand_index_list(spec: str) -> List[int]:
    out: List[int] = []
    for chunk in spec.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        if "-" in chunk:
            lo, hi = chunk.split("-", 1)
            try:
                a, b = int(lo), int(hi)
            except ValueError:
                continue
            if a <= b:
                out.extend(range(a, b + 1))
        else:
            try:
                out.append(int(chunk))
            except ValueError:
                continue
    seen = set()
    return [x for x in out if not (x in seen or seen.add(x))]


def merge_plan_skeleton(topology: Topology, results: Dict[str, Any]) -> str:
    """Render a `WORKSPACES:` block for the joiner scratchpad.

    `results` maps group name → AgentResult-like (or any object with
    `branch` / `commits` attrs). Empty when topology is single-group.
    """
    if not topology.is_multi:
        return ""
    lines = ["WORKSPACES:"]
    for name in topology.groups:
        r = results.get(name)
        branch = getattr(r, "branch", None) or "?"
        commits = getattr(r, "commits", []) or []
        lines.append(f"  {name}: branch={branch} commits={len(commits)}")
    return "\n".join(lines)
