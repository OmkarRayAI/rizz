"""Phase 3 E.3 — Conflict detection smoke test.

Two stub agents both append to README.md (the same file the init repo
created), then `detect_conflicts` between their branches should return
exactly one ConflictPair listing README.md.

Run: python examples/conflict_smoke.py
"""

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rizz.agents.agent_result import AgentResult  # noqa: E402
from rizz.agents.base_agent import CodingAgent  # noqa: E402
from rizz.conflicts import detect_conflicts, render_conflict_report  # noqa: E402
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
    (root / "README.md").write_text("# original line\n")
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", "init"], check=True
    )


class ReadmeAppender(CodingAgent):
    """Appends a unique line to README.md and commits."""

    async def run(self, goal: str, workspace) -> AgentResult:
        readme = workspace.cwd / "README.md"
        existing = readme.read_text() if readme.exists() else ""
        readme.write_text(existing + f"line from {self.name}: {goal}\n")
        await workspace.run_git("add", "-A")
        await workspace.run_git("commit", "-m", f"[stub] {self.name}: append")
        sha = (await workspace.run_git("rev-parse", "HEAD")).strip()
        return await AgentResult.from_workspace(
            workspace,
            summary=f"{self.name} appended to README.md",
            new_commits=[sha],
        )


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="rizz-conflict-"))
    repo = tmp / "repo"
    repo.mkdir()
    _init_test_repo(repo)
    try:
        mgr = WorkspaceManager(repo=repo)
        ws_a = await mgr.allocate(branch_hint="wsA")
        ws_b = await mgr.allocate(branch_hint="wsB")
        try:
            agent_a = ReadmeAppender("agent_a", "appends A").get_tool()
            agent_b = ReadmeAppender("agent_b", "appends B").get_tool()

            unit = TaskFetchingUnit(
                workspaces={"wsA": ws_a, "wsB": ws_b},
                default_group="wsA",
            )
            unit.set_tasks(
                {
                    1: Task(
                        idx=1,
                        name="agent_a",
                        tool=agent_a.coroutine,
                        args=("hello A",),
                        dependencies=[],
                        workspace_group="wsA",
                    ),
                    2: Task(
                        idx=2,
                        name="agent_b",
                        tool=agent_b.coroutine,
                        args=("hello B",),
                        dependencies=[],
                        workspace_group="wsB",
                    ),
                }
            )
            await unit.schedule()

            pairs = await detect_conflicts(repo, [ws_a.branch, ws_b.branch])
            assert len(pairs) == 1, f"expected 1 conflict pair, got {len(pairs)}: {pairs}"
            pair = pairs[0]
            assert "README.md" in pair.files, (
                f"expected README.md in conflict files, got {pair.files}"
            )
            assert {pair.branch_a, pair.branch_b} == {ws_a.branch, ws_b.branch}
            print(f"conflict detected: {pair.branch_a} vs {pair.branch_b}")
            print(f"  files: {pair.files}")

            report = render_conflict_report(pairs)
            assert "CONFLICTS:" in report
            assert "README.md" in report
            print("render_conflict_report: OK")
            print(report)

            # Empty branch list: no conflicts.
            none = await detect_conflicts(repo, [])
            assert none == []

            # Single branch: no conflicts.
            single = await detect_conflicts(repo, [ws_a.branch])
            assert single == []
            print("conflict_smoke: PASS")
        finally:
            await mgr.cleanup_all()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
