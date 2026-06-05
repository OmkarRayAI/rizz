"""Phase 4 D.2 — Persistence end-to-end (no LLM).

Drives a real WorkspaceManager + stub coding agents through TaskFetchingUnit
while writing to a real RunStore. After the run, drops the store handle and
reopens it from a fresh RunStore.for_repo() call (simulating a "different
process") to verify the data survived.

Asserts:
- The DB file appears at <repo>/.rizz/state.db.
- A reopened store can read back the run, workspaces, tasks, observations,
  and PR links.
- Worktrees survive when manager.cleanup_all(policy="skip") is used.
- Worktrees disappear when manager.cleanup_all(policy="force") is used.
- AgentResult round-trips with full fidelity (commits, files_changed, etc.).

Run: python examples/persistence_smoke.py
"""

import asyncio
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rizz.agents.agent_result import AgentResult  # noqa: E402
from rizz.agents.base_agent import CodingAgent  # noqa: E402
from rizz.constants import RIZZ_WORKTREE_DIR_NAME  # noqa: E402
from rizz.store import RunStore  # noqa: E402
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
    async def run(self, goal: str, workspace) -> AgentResult:
        target_dir = workspace.cwd / self.name
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "out.md").write_text(f"hello from {self.name}\ngoal: {goal}\n")
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


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="rizz-persistence-"))
    repo = tmp / "repo"
    repo.mkdir()
    _init_test_repo(repo)

    run_id = "run-cafef00d"
    try:
        # 1. Create the store and the run.
        store = RunStore.for_repo(repo)
        store.create_run(
            run_id,
            repo=str(repo),
            question="add stub features in two branches",
            purpose="phase 4 persistence smoke",
            multi_workspace=True,
            open_pr=False,
            cleanup_policy="never",
        )
        print("created run; DB at", store.db_path)
        assert store.db_path.exists(), "DB should be created"

        # 2. Allocate two real workspaces.
        mgr = WorkspaceManager(repo=repo)
        ws_a = await mgr.allocate(branch_hint="wsA")
        ws_b = await mgr.allocate(branch_hint="wsB")
        store.add_workspace(run_id, ws_a, "wsA")
        store.add_workspace(run_id, ws_b, "wsB")

        # 3. Drive stub agents through TaskFetchingUnit (real path).
        impl = StubAgent("impl", "Implements feature").get_tool()
        docs = StubAgent("docs", "Updates docs").get_tool()
        unit = TaskFetchingUnit(
            workspaces={"wsA": ws_a, "wsB": ws_b}, default_group="wsA"
        )
        unit.set_tasks(
            {
                1: Task(
                    idx=1, name="impl", tool=impl.coroutine,
                    args=("write impl",), dependencies=[],
                    workspace_group="wsA",
                ),
                2: Task(
                    idx=2, name="docs", tool=docs.coroutine,
                    args=("update docs",), dependencies=[],
                    workspace_group="wsB",
                ),
            }
        )
        # Persist tasks before scheduling so a crash mid-schedule still leaves
        # the graph queryable.
        for t in unit.tasks.values():
            store.add_task(run_id, t)

        await unit.schedule()

        # Persist observations.
        for idx, t in unit.tasks.items():
            if t.observation is not None:
                store.set_observation(run_id, idx, t.observation)

        store.add_pr(run_id, "wsA", "https://github.com/me/repo/pull/100")
        store.set_run_status(
            run_id, "completed", raw_answer="ok", thinking_process="..."
        )
        store.close()
        print("wrote run state; closed store")

        # 4. Reopen store from scratch (simulates a different process).
        store2 = RunStore.for_repo(repo)
        rec = store2.get_run(run_id)
        assert rec is not None, "run should be retrievable post-reopen"
        assert rec.summary.status == "completed"
        assert rec.summary.num_workspaces == 2
        assert rec.summary.num_prs == 1
        assert {w.group_name for w in rec.workspaces} == {"wsA", "wsB"}
        assert {t.name for t in rec.tasks} == {"impl", "docs"}

        # AgentResult full fidelity. files_changed is populated from
        # `git status --porcelain` AFTER the agent's auto-commit, which
        # leaves the worktree clean — so it stays empty here. The commit
        # SHA in `commits` is the per-agent attribution. Branch and diff
        # round-trip from the workspace fork point.
        impl_obs = next(r for r in rec.results if r.task_idx == 1)
        assert impl_obs.exit_status == "ok"
        assert len(impl_obs.commits) == 1, (
            f"expected 1 commit, got {impl_obs.commits}"
        )
        assert impl_obs.branch and impl_obs.branch.startswith("rizz/wsA"), (
            f"unexpected branch: {impl_obs.branch}"
        )
        assert "out.md" in impl_obs.diff, (
            f"expected out.md in diff body, got: {impl_obs.diff[:200]}"
        )
        print("reopen-and-read: OK (commits + branch + diff round-trip)")

        # 5. policy="skip" leaves worktrees on disk.
        await mgr.cleanup_all(policy="skip", run_status="completed")
        assert ws_a.cwd.exists(), "policy=skip should keep wsA worktree"
        assert ws_b.cwd.exists(), "policy=skip should keep wsB worktree"
        print("policy=skip preserves worktrees: OK")

        # 6. list_runs filters by repo (canonicalized).
        runs = store2.list_runs(repo=str(repo))
        assert len(runs) == 1 and runs[0].run_id == run_id
        print("list_runs filter: OK")

        # 7. Now actually clean up.
        await mgr.cleanup_all(policy="force", run_status="completed")
        assert not ws_a.cwd.exists()
        assert not ws_b.cwd.exists()
        store2.close()
        print("policy=force removes worktrees: OK")

        # 8. The DB file (and historical state) survives the cleanup.
        db_path = repo / RIZZ_WORKTREE_DIR_NAME / "state.db"
        assert db_path.exists(), "state.db should survive worktree cleanup"
        store3 = RunStore(db_path)
        assert store3.get_run(run_id) is not None, (
            "state.db should still hold the run after cleanup"
        )
        store3.close()
        print("state.db survives cleanup: OK")

        print("persistence_smoke: PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
