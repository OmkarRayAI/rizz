"""Phase 4 D.1 — RunStore exercise (no LLM, no engine).

Verifies the SQLite layer in isolation:
- Schema initializes from scratch.
- create_run / add_workspace / add_task / set_observation / add_pr / set_run_status all roundtrip.
- get_run reconstructs the full RunRecord with embedded children.
- list_runs filters by repo and status.
- delete_run cascades to children.
- Mixed observation types (AgentResult and plain string) both persist.

Run: python examples/store_smoke.py
"""

import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rizz.agents.agent_result import AgentResult  # noqa: E402
from rizz.run_record import RunRecord  # noqa: E402
from rizz.store import RunStore  # noqa: E402
from rizz.task_fetching_unit import Task  # noqa: E402


def _fake_workspace(wsid: str, branch: str, cwd: str) -> SimpleNamespace:
    """Duck-typed Workspace stand-in for store testing without git/asyncio."""
    return SimpleNamespace(
        id=wsid,
        branch=branch,
        base_ref="abc1234567",
        cwd=cwd,
        _owns_worktree=True,
    )


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="rizz-store-"))
    try:
        store = RunStore(tmp / "state.db")

        # 1. Create a run
        store.create_run(
            "run-deadbeef",
            repo="/tmp/fake-repo",
            question="implement /login and document it",
            purpose="phase 4 smoke",
            multi_workspace=True,
            open_pr=False,
            cleanup_policy="never",
        )

        # 2. Add workspaces
        store.add_workspace(
            "run-deadbeef",
            _fake_workspace("wk-aaa111", "rizz/wsA-wk-aaa111", "/tmp/wsA"),
            "wsA",
        )
        store.add_workspace(
            "run-deadbeef",
            _fake_workspace("wk-bbb222", "rizz/wsB-wk-bbb222", "/tmp/wsB"),
            "wsB",
        )

        # 3. Add tasks
        store.add_task(
            "run-deadbeef",
            Task(
                idx=1,
                name="impl_agent",
                tool=lambda: None,
                args=("Add /login route",),
                dependencies=[],
                workspace_group="wsA",
            ),
        )
        store.add_task(
            "run-deadbeef",
            Task(
                idx=2,
                name="docs_agent",
                tool=lambda: None,
                args=("Update API.md",),
                dependencies=[],
                workspace_group="wsB",
            ),
        )

        # 4. Mixed observations: AgentResult on task 1, plain string on task 2
        store.set_observation(
            "run-deadbeef",
            1,
            AgentResult(
                summary="impl_agent: added /login returning 401",
                diff="--- a/auth.py\n+++ b/auth.py\n@@\n+...\n",
                files_changed=["auth.py", "tests/test_auth.py"],
                branch="rizz/wsA-wk-aaa111",
                commits=["c0ffee1234567890"],
                exit_status="ok",
            ),
        )
        store.set_observation("run-deadbeef", 2, "docs_agent: appended API.md section")

        # 5. PR link
        store.add_pr("run-deadbeef", "wsA", "https://github.com/me/repo/pull/42")

        # 6. Mark complete
        store.set_run_status(
            "run-deadbeef",
            "completed",
            raw_answer="Both branches ready; opened wsA PR.",
            thinking_process="meta plan generated successfully",
        )

        # --- reads ---
        rec = store.get_run("run-deadbeef")
        assert isinstance(rec, RunRecord)
        assert rec.summary.status == "completed"
        assert rec.summary.num_workspaces == 2
        assert rec.summary.num_prs == 1
        assert rec.purpose == "phase 4 smoke"
        assert rec.cleanup_policy == "never"
        assert len(rec.workspaces) == 2 and {w.group_name for w in rec.workspaces} == {"wsA", "wsB"}
        assert len(rec.tasks) == 2
        assert rec.tasks[0].args == ["Add /login route"]
        assert len(rec.results) == 2

        # AgentResult observation roundtrips fully
        ar = next(r for r in rec.results if r.task_idx == 1)
        assert ar.exit_status == "ok"
        assert ar.commits == ["c0ffee1234567890"]
        assert "auth.py" in ar.files_changed
        assert ar.branch == "rizz/wsA-wk-aaa111"

        # Plain-string observation lands in summary, exit_status defaults to ok
        sr = next(r for r in rec.results if r.task_idx == 2)
        assert sr.summary == "docs_agent: appended API.md section"
        assert sr.diff == ""
        assert sr.commits == []

        # PRs
        assert len(rec.prs) == 1
        assert rec.prs[0].url.endswith("/pull/42")
        assert rec.prs[0].group_name == "wsA"

        print("create+read roundtrip: OK")

        # 7. list_runs filters
        runs_by_repo = store.list_runs(repo="/tmp/fake-repo")
        assert len(runs_by_repo) == 1
        assert runs_by_repo[0].run_id == "run-deadbeef"

        runs_completed = store.list_runs(status="completed")
        assert len(runs_completed) == 1

        runs_failed = store.list_runs(status="failed")
        assert runs_failed == []

        runs_other_repo = store.list_runs(repo="/tmp/elsewhere")
        assert runs_other_repo == []

        print("list_runs filters: OK")

        # 8. delete cascades
        store.delete_run("run-deadbeef")
        assert store.get_run("run-deadbeef") is None
        assert store.get_workspaces("run-deadbeef") == []
        assert store.get_agent_results("run-deadbeef") == []
        assert store.get_pr_links("run-deadbeef") == []
        print("cascade delete: OK")

        # 9. Re-open from a fresh store object — schema survives.
        store.close()
        store2 = RunStore(tmp / "state.db")
        assert store2.list_runs() == []
        store2.close()
        print("reopen across store instances: OK")

        print("store_smoke: PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
