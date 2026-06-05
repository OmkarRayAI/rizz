"""Phase 3 E.2 — Multi-workspace smoke test (no LLM).

Drives `TaskFetchingUnit` directly with two workspaces and a 4-task DAG
matching the worked example from the plan:

  [wsA] 1. impl_agent("write impl")
  [wsA] 2. test_agent("verify $1.summary")
  [wsB] 3. docs_agent("update docs")
  [wsA] 4. join()

Asserts:
- Each task ran in the workspace its group maps to.
- HEADs of the two branches advanced independently
  (wsA: 2 commits, wsB: 1 commit).
- `merge_tree` between the two branches is clean (disjoint files).
- The Phase-1 single-workspace mode still works when constructed with
  `workspace=` instead of `workspaces=`.

Run: python examples/multi_workspace_smoke.py
"""

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rizz import git_utils  # noqa: E402
from rizz.agents.agent_result import AgentResult  # noqa: E402
from rizz.agents.base_agent import CodingAgent  # noqa: E402
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


class StubAgent(CodingAgent):
    """Mutates a file under <cwd>/<self.name>/, auto-commits, returns
    an AgentResult. The per-name subdir keeps file paths disjoint."""

    async def run(self, goal: str, workspace) -> AgentResult:
        target_dir = workspace.cwd / self.name
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "out.md"
        target.write_text(f"hello from {self.name}\ngoal: {goal}\n")
        await workspace.run_git("add", "-A")
        await workspace.run_git(
            "commit", "-m", f"[stub] {self.name}: {goal[:48]}"
        )
        sha = (await workspace.run_git("rev-parse", "HEAD")).strip()
        return await AgentResult.from_workspace(
            workspace,
            summary=f"{self.name} wrote {target_dir.name}/out.md",
            new_commits=[sha],
        )


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="rizz-multi-ws-"))
    repo = tmp / "repo"
    repo.mkdir()
    _init_test_repo(repo)
    try:
        mgr = WorkspaceManager(repo=repo)
        ws_a = await mgr.allocate(branch_hint="wsA")
        ws_b = await mgr.allocate(branch_hint="wsB")
        try:
            base_sha = (await ws_a.run_git("rev-parse", "HEAD")).strip()

            impl = StubAgent("impl", "Implements feature").get_tool()
            test = StubAgent("test", "Adds test").get_tool()
            docs = StubAgent("docs", "Updates docs").get_tool()

            unit = TaskFetchingUnit(
                workspaces={"wsA": ws_a, "wsB": ws_b},
                default_group="wsA",
            )
            unit.set_tasks(
                {
                    1: Task(
                        idx=1,
                        name="impl",
                        tool=impl.coroutine,
                        args=("write impl",),
                        dependencies=[],
                        workspace_group="wsA",
                    ),
                    2: Task(
                        idx=2,
                        name="test",
                        tool=test.coroutine,
                        args=("verify $1.summary",),
                        dependencies=[1],
                        workspace_group="wsA",
                    ),
                    3: Task(
                        idx=3,
                        name="docs",
                        tool=docs.coroutine,
                        args=("update docs",),
                        dependencies=[],
                        workspace_group="wsB",
                    ),
                    4: Task(
                        idx=4,
                        name="join",
                        tool=lambda x: None,
                        args=(),
                        dependencies=[1, 2, 3],
                        is_join=True,
                        workspace_group="wsA",
                    ),
                }
            )
            await unit.schedule()

            r1 = unit.tasks[1].observation
            r2 = unit.tasks[2].observation
            r3 = unit.tasks[3].observation

            # 1. Each task ran in its expected workspace.
            assert unit.tasks[1].workspace.cwd == ws_a.cwd, (
                f"task 1 expected {ws_a.cwd}, got {unit.tasks[1].workspace.cwd}"
            )
            assert unit.tasks[2].workspace.cwd == ws_a.cwd
            assert unit.tasks[3].workspace.cwd == ws_b.cwd
            print("per-group workspace routing: OK")

            # 2. $1.summary substituted using AgentResult attribute.
            assert isinstance(r1, AgentResult)
            assert "verify impl wrote impl/out.md" in unit.tasks[2].args[0], (
                f"unexpected substituted args: {unit.tasks[2].args[0]!r}"
            )
            print("$1.summary substitution: OK")

            # 3. Branch HEADs advanced independently.
            log_a = (
                await ws_a.run_git("log", "--oneline", f"{base_sha}..HEAD")
            ).strip().splitlines()
            log_b = (
                await ws_b.run_git("log", "--oneline", f"{base_sha}..HEAD")
            ).strip().splitlines()
            assert len(log_a) == 2, f"wsA expected 2 commits, got {len(log_a)}: {log_a}"
            assert len(log_b) == 1, f"wsB expected 1 commit, got {len(log_b)}: {log_b}"
            print(f"wsA: {len(log_a)} commits, wsB: {len(log_b)} commits — OK")

            # 4. merge_tree between disjoint branches is clean.
            clean, files = await git_utils.merge_tree(
                repo, ws_a.branch, ws_b.branch
            )
            assert clean, f"expected clean merge; got conflicts {files}"
            print("merge_tree clean for disjoint branches: OK")

            # 5. Phase-1 single-ws mode still works.
            unit_single = TaskFetchingUnit(workspace=ws_a)
            assert unit_single.workspace is ws_a
            assert unit_single.workspaces == {}
            print("Phase-1 single-ws mode preserved: OK")

            assert isinstance(r3, AgentResult) and r3.exit_status == "ok"
            print("multi_workspace_smoke: PASS")
        finally:
            await mgr.cleanup_all()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
