"""Phase 3 E.1 — Pure parser test for `parse_topology_block`.

No LLM, no git. Verifies the WORKSPACE_TOPOLOGY block grammar:
- Valid block with default → groups parsed, default set.
- Unknown default → falls back to first group.
- Overlapping indices → first wins.
- `tasks 1-3` range syntax → expanded.
- No block / empty / None → empty Topology, no exception.

Run: python examples/topology_smoke.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rizz.topology import Topology, parse_topology_block  # noqa: E402


def main() -> None:
    # --- 1. Valid block with default ---
    text = (
        "META PLAN: ...\n\n"
        "WORKSPACE_TOPOLOGY:\n"
        "  wsA: 1, 2\n"
        "  wsB: 3, 4\n"
        "  default: wsA\n"
        "\n"
        "More prose follows.\n"
    )
    t = parse_topology_block(text)
    assert t.is_multi
    assert t.groups == {"wsA": [1, 2], "wsB": [3, 4]}, t.groups
    assert t.default_group == "wsA"
    assert t.for_task(2) == "wsA"
    assert t.for_task(4) == "wsB"
    assert t.for_task(99) == "wsA"  # falls through to default
    print("valid block + default: OK")

    # --- 2. Unknown default → first group wins ---
    text = (
        "WORKSPACE_TOPOLOGY:\n"
        "  wsA: 1\n"
        "  wsB: 2\n"
        "  default: wsZ\n"
    )
    t = parse_topology_block(text)
    assert t.default_group == "wsA", t.default_group
    print("unknown default → first group: OK")

    # --- 3. Overlap → first claim wins ---
    text = (
        "WORKSPACE_TOPOLOGY:\n"
        "  wsA: 1, 2\n"
        "  wsB: 2, 3\n"
    )
    t = parse_topology_block(text)
    assert t.groups["wsA"] == [1, 2], t.groups["wsA"]
    assert t.groups["wsB"] == [3], t.groups["wsB"]
    print("overlap → first wins: OK")

    # --- 4. Range syntax ---
    text = (
        "WORKSPACE_TOPOLOGY:\n"
        "  wsA: tasks 1-3, 5\n"
        "  wsB: 4\n"
    )
    t = parse_topology_block(text)
    assert t.groups["wsA"] == [1, 2, 3, 5], t.groups["wsA"]
    assert t.groups["wsB"] == [4]
    print("range syntax: OK")

    # --- 5. No block ---
    for empty in ["", "META PLAN: just prose, no topology.", None]:
        t = parse_topology_block(empty)
        assert not t.is_multi
        assert t.groups == {}
        assert t.default_group is None
    print("no block / empty / None → empty Topology: OK")

    # --- 6. Malformed lines silently dropped ---
    text = (
        "WORKSPACE_TOPOLOGY:\n"
        "  wsA: 1, 2\n"
        "  garbage line that doesn't match anything\n"
        "  wsB: 3\n"
    )
    t = parse_topology_block(text)
    assert t.groups == {"wsA": [1, 2], "wsB": [3]}
    print("malformed lines dropped: OK")

    # --- 7. Single-group block → not multi but parsed ---
    text = "WORKSPACE_TOPOLOGY:\n  wsA: 1, 2, 3\n"
    t = parse_topology_block(text)
    assert not t.is_multi  # single group → fall back to single-ws mode
    assert t.groups == {"wsA": [1, 2, 3]}
    print("single-group block → not multi: OK")

    print("topology_smoke: PASS")


if __name__ == "__main__":
    main()
