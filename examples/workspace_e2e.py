"""Phase 1 D.3 — End-to-end test of workspace injection.

Bypasses the LLM/planner layer (we don't have an API key and we're not testing
prompt quality here) and directly drives a TaskFetchingUnit with two tasks:

1. note_writer(message: str, workspace) — opts in via signature
2. legacy_tool(query: str)             — does NOT opt in; must run unchanged

Verifies:
- workspace handle is injected into the opt-in tool
- legacy tool sees no `workspace=` kwarg (negative control for the schema-leak risk)
- notes folder contains the expected file during the run
- worktree is cleaned up after the run

Run: python examples/workspace_e2e.py
"""

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rizz.task_fetching_unit import Task, TaskFetchingUnit  # noqa: E402
from rizz.workspace_manager import WorkspaceManager  # noqa: E402


def _init_test_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "test"], check=True
    )
    (root / "README.md").write_text("# test\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", "init"], check=True
    )


legacy_received_kwargs: dict = {}
note_path_box: dict = {}


async def note_writer(message: str, workspace) -> str:
    path = await workspace.write_note("agent.md", message)
    note_path_box["path"] = path
    return str(path)


async def legacy_tool(query: str, **kwargs) -> str:
    legacy_received_kwargs.update(kwargs)
    return f"legacy:{query}"


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="rizz-e2e-"))
    repo = tmp / "repo"
    repo.mkdir()
    _init_test_repo(repo)
    try:
        mgr = WorkspaceManager(repo=repo)
        ws = await mgr.allocate()
        try:
            unit = TaskFetchingUnit(workspace=ws)
            unit.set_tasks(
                {
                    1: Task(
                        idx=1,
                        name="note_writer",
                        tool=note_writer,
                        args=("hello from a phase-1 task",),
                        dependencies=[],
                    ),
                    2: Task(
                        idx=2,
                        name="legacy_tool",
                        tool=legacy_tool,
                        args=("ping",),
                        dependencies=[],
                    ),
                }
            )
            await unit.schedule()

            # opt-in tool got the workspace and wrote the note
            assert note_path_box["path"].exists(), "note file should exist"
            assert (
                note_path_box["path"].read_text() == "hello from a phase-1 task"
            ), "note content mismatch"
            assert (
                unit.tasks[1].observation == str(note_path_box["path"])
            ), f"unexpected observation: {unit.tasks[1].observation!r}"
            print(f"note_writer wrote: {note_path_box['path']}")

            # legacy tool ran unchanged, never saw workspace
            assert (
                "workspace" not in legacy_received_kwargs
            ), f"legacy tool received workspace kwarg: {legacy_received_kwargs!r}"
            assert unit.tasks[2].observation == "legacy:ping"
            print("legacy_tool unaffected by workspace plumbing")
        finally:
            await mgr.cleanup_all()
        assert not ws.cwd.exists(), "worktree should be removed on cleanup"
        print("worktree cleaned up")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("workspace_e2e: PASS")


if __name__ == "__main__":
    asyncio.run(main())
