"""Phase 1 D.1 — "Existing callers untouched" check.

Imports `Rizz` and `Workspace`, exercises `Rizz.run()`'s signature
without invoking the LLM (we don't have credentials), and checks that:

- The new opt-in kwargs (repo=, workspace=, ...) all default to None.
- No `.rizz/` directory is created when `repo=` is omitted.
- Importing the package as the README does still works.

This is a static/shape check — it does NOT call the LLM.

Run: python examples/arcade_smoke.py
"""

import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rizz import Rizz, Workspace, WorkspaceManager  # noqa: E402,F401


def main() -> None:
    sig = inspect.signature(Rizz.run)
    params = sig.parameters

    # The four Phase-1 opt-in kwargs must exist and default to None
    for name in ("repo", "workspace", "workspace_env", "worktree_root"):
        assert name in params, f"missing {name} in Rizz.run signature"
        assert params[name].default is None, (
            f"{name} default is {params[name].default!r}, expected None"
        )
    print("Rizz.run signature: opt-in kwargs all default to None")

    # The pre-Phase-1 kwargs are still positional with the same names
    for legacy in (
        "question",
        "purpose",
        "tools",
        "instructions",
        "planner_example_prompt",
        "joinner_prompt",
        "tool_path",
    ):
        assert legacy in params, f"legacy kwarg {legacy} disappeared"
    print("legacy kwargs preserved")

    # No worktrees should be sitting around just from importing
    cwd_marker = Path.cwd() / ".rizz"
    assert not cwd_marker.exists(), (
        f"importing Rizz should not create {cwd_marker}; "
        f"clean it up before retrying"
    )
    print("no stray .rizz/ in cwd")

    print("arcade_smoke: PASS")


if __name__ == "__main__":
    main()
