"""Phase 3 E.4 — Auto-PR documentation example (gated).

Skips with a friendly message unless:
- `gh` CLI is on PATH and authenticated (`gh auth login` done).
- RIZZ_TEST_PR_REPO is set to a clone URL the caller controls
  (e.g. `git@github.com:you/scratch-repo.git`). The script clones
  into a tempdir, allocates two workspaces, runs stub agents that
  touch disjoint files, then `gh pr create`s draft PRs.

This is documentation, not CI. Don't run it against a real repo unless
you're prepared to delete the resulting branches and PRs.

Run: ANTHROPIC_API_KEY ignored. RIZZ_TEST_PR_REPO=... python examples/auto_pr_smoke.py
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rizz import gh_utils, git_utils  # noqa: E402
from rizz.agents.agent_result import AgentResult  # noqa: E402
from rizz.agents.base_agent import CodingAgent  # noqa: E402
from rizz.task_fetching_unit import Task, TaskFetchingUnit  # noqa: E402
from rizz.workspace_manager import WorkspaceManager  # noqa: E402


class StubAgent(CodingAgent):
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
    if shutil.which("gh") is None:
        print("`gh` CLI not on PATH; skipping. Install via `brew install gh`.")
        return
    if not await gh_utils.is_gh_available():
        print("`gh` reports unavailable; skipping.")
        return
    clone_url = os.environ.get("RIZZ_TEST_PR_REPO")
    if not clone_url:
        print(
            "RIZZ_TEST_PR_REPO not set; skipping. "
            "Set it to a scratch repo clone URL you own."
        )
        return

    tmp = Path(tempfile.mkdtemp(prefix="rizz-auto-pr-"))
    try:
        # Clone the test repo
        repo = tmp / "repo"
        subprocess.run(
            ["git", "clone", "-q", clone_url, str(repo)], check=True
        )
        print(f"cloned {clone_url} → {repo}")

        # Detect default branch.
        base = await git_utils.get_default_branch(repo)
        if base is None:
            print("could not detect default branch; aborting.")
            return
        print(f"default branch: {base}")

        mgr = WorkspaceManager(repo=repo)
        ws_a = await mgr.allocate(branch_hint="autopr_wsA")
        ws_b = await mgr.allocate(branch_hint="autopr_wsB")
        try:
            stub_a = StubAgent("autopr_a", "Stub PR A").get_tool()
            stub_b = StubAgent("autopr_b", "Stub PR B").get_tool()
            unit = TaskFetchingUnit(
                workspaces={"wsA": ws_a, "wsB": ws_b},
                default_group="wsA",
            )
            unit.set_tasks(
                {
                    1: Task(
                        idx=1, name="autopr_a", tool=stub_a.coroutine,
                        args=("hello A",), dependencies=[],
                        workspace_group="wsA",
                    ),
                    2: Task(
                        idx=2, name="autopr_b", tool=stub_b.coroutine,
                        args=("hello B",), dependencies=[],
                        workspace_group="wsB",
                    ),
                }
            )
            await unit.schedule()

            urls = []
            for group, ws in [("wsA", ws_a), ("wsB", ws_b)]:
                await git_utils.push(repo, ws.branch)
                url = await gh_utils.gh_pr_create(
                    repo,
                    base=base,
                    head=ws.branch,
                    title=f"[{group}] rizz auto-PR smoke",
                    body=(
                        f"Stub PR opened by examples/auto_pr_smoke.py.\n\n"
                        f"Group: {group}\nBranch: {ws.branch}\n"
                    ),
                    draft=True,
                )
                urls.append(url)
                print(f"opened draft PR for {group}: {url}")
            print("auto_pr_smoke: PASS")
            print("\nDon't forget to close these PRs and delete the branches:")
            for u in urls:
                print(f"  {u}")
        finally:
            await mgr.cleanup_all()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
