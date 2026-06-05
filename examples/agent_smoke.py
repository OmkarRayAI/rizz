"""Phase 2 E.1 — Stub-agent smoke test (no SDK).

Verifies the Phase 2 contract end-to-end without invoking Claude:
- A `CodingAgent` subclass mutates files in `workspace.cwd`, auto-commits,
  and returns an `AgentResult`.
- The result's `__str__` renders in the bounded format the joiner expects.
- A second task uses `$1.summary` and sees the substituted text.
- Both tasks are scheduled through `TaskFetchingUnit`, the same path the
  real LLMCompiler uses.

Run: python examples/agent_smoke.py
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
    """Mutates a file, auto-commits, returns an AgentResult."""

    async def run(self, goal: str, workspace) -> AgentResult:
        target = workspace.cwd / f"{self.name}.md"
        target.write_text(f"hello from {self.name}\ngoal: {goal}\n")
        await workspace.run_git("add", "-A")
        await workspace.run_git(
            "commit", "-m", f"[stub] {self.name}: {goal[:48]}"
        )
        sha = (await workspace.run_git("rev-parse", "HEAD")).strip()
        return await AgentResult.from_workspace(
            workspace,
            summary=f"{self.name} wrote {target.name}",
            new_commits=[sha],
        )


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="rizz-agent-smoke-"))
    repo = tmp / "repo"
    repo.mkdir()
    _init_test_repo(repo)
    try:
        mgr = WorkspaceManager(repo=repo)
        ws = await mgr.allocate()
        try:
            base_sha = (await ws.run_git("rev-parse", "HEAD")).strip()

            impl = StubAgent("impl", "Implements a stub feature")
            test = StubAgent("test", "Adds a stub test")
            impl_tool = impl.get_tool()
            test_tool = test.get_tool()

            unit = TaskFetchingUnit(workspace=ws)
            unit.set_tasks(
                {
                    1: Task(
                        idx=1,
                        name="impl",
                        tool=impl_tool.coroutine,
                        args=("write a hello",),
                        dependencies=[],
                    ),
                    2: Task(
                        idx=2,
                        name="test",
                        tool=test_tool.coroutine,
                        args=("verify $1.summary",),
                        dependencies=[1],
                    ),
                }
            )
            await unit.schedule()

            r1 = unit.tasks[1].observation
            r2 = unit.tasks[2].observation

            # Result shape
            assert isinstance(r1, AgentResult), f"expected AgentResult, got {type(r1)}"
            assert isinstance(r2, AgentResult)
            assert r1.exit_status == "ok"
            assert len(r1.commits) == 1, f"expected 1 commit, got {r1.commits}"
            print(f"impl observation: {r1.summary} commits={[c[:8] for c in r1.commits]}")

            # __str__ format
            s1 = str(r1)
            assert s1.startswith("[ok] impl wrote impl.md"), repr(s1[:80])
            assert "--- diff ---" in s1
            print("AgentResult.__str__ format OK")

            # $1.summary substitution
            substituted_args = unit.tasks[2].args
            assert isinstance(substituted_args, list), \
                f"args should be list after substitution: {type(substituted_args)}"
            assert "verify impl wrote impl.md" in substituted_args[0], \
                f"substitution mismatch: {substituted_args[0]!r}"
            print(f"$1.summary substituted to: {substituted_args[0]!r}")

            # HEAD advanced twice from base_ref
            head_now = (await ws.run_git("rev-parse", "HEAD")).strip()
            log_lines = (
                await ws.run_git("log", "--oneline", f"{base_sha}..{head_now}")
            ).strip().splitlines()
            assert len(log_lines) == 2, \
                f"expected 2 new commits since base, got {len(log_lines)}: {log_lines}"
            print(f"HEAD advanced 2 commits: {log_lines}")
        finally:
            await mgr.cleanup_all()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("agent_smoke: PASS")


if __name__ == "__main__":
    asyncio.run(main())
