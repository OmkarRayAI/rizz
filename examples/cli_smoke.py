"""Phase 4 D.3 — End-to-end CLI smoke test (no real LLM, no real network).

Exercises the `rizz` CLI as a subprocess against a tempdir test repo:

  1. `rizz run` against a stub LLM tools-config that produces a
     deterministic single-task plan.
  2. `rizz list --repo R` shows the run.
  3. `rizz status <id>` renders detail.
  4. `rizz prs <id>` exits 0 (empty since open_pr=False).
  5. `rizz clean --keep-recent 0 --dry-run` previews.
  6. `rizz clean --keep-recent 0` actually deletes; subsequent `list` is empty.

Tests: console-script entry point, argument parsing, full command tree,
exit codes, persistence write/read across CLI invocations.

Run: python examples/cli_smoke.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from textwrap import dedent

REPO_ROOT = Path(__file__).resolve().parents[1]


def _git(args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_test_repo(root: Path) -> None:
    _git(["init", "-q", "-b", "main", str(root)], cwd=str(root.parent))
    _git(["config", "user.email", "test@example.com"], cwd=str(root))
    _git(["config", "user.name", "test"], cwd=str(root))
    (root / "README.md").write_text("# test\n")
    _git(["add", "."], cwd=str(root))
    _git(["commit", "-q", "-m", "init"], cwd=str(root))


# Tools config: defines a stub LLM that returns canned text per call site.
TOOLS_CONFIG_SRC = dedent(
    '''
    """Stub LLM and tool registration for cli_smoke.py."""
    import asyncio
    from typing import Any, List, Optional

    from langchain.llms.base import LLM
    from pydantic import BaseModel

    from rizz.agents.agent_result import AgentResult
    from rizz.agents.base_agent import CodingAgent
    from rizz.base import StructuredTool


    # ---- stub coding agent: doesn't need a workspace, returns OK ----
    class _StubAgent(CodingAgent):
        async def run(self, goal: str, workspace) -> AgentResult:
            return AgentResult(
                summary=f"stub: {goal[:80]}",
                exit_status="ok",
                branch=getattr(workspace, "branch", None),
            )


    class _StubAgentTool:
        def __init__(self, **kwargs):
            self._agent = _StubAgent(
                name=kwargs.get("name", "stub_agent"),
                description=kwargs.get("extra_info", "stub coding agent"),
            )

        def get_tool(self) -> StructuredTool:
            return self._agent.get_tool()


    # ---- stub LLM: route by prompt content -----------------------
    class _StubLLM(LLM):
        @property
        def _llm_type(self) -> str:
            return "stub"

        def _call(
            self,
            prompt: str,
            stop: Optional[List[str]] = None,
            run_manager: Any = None,
            **kwargs: Any,
        ) -> str:
            # Joiner: it asks for Thought/Action with Finish/Replan.
            if "Action:" in prompt and ("Finish" in prompt or "Replan" in prompt):
                return (
                    "Thought: stub task completed.\\n"
                    "Action: Finish(stub run completed via cli_smoke)"
                )
            # Planner: it lists tools and asks for `<idx>. tool(...)` form.
            if "Given a user query and meta plan" in prompt:
                return (
                    "Thought: do the stub task and join.\\n"
                    "1. stub_agent(\\"do the thing\\")\\n"
                    "2. join() <END_OF_PLAN>\\n"
                )
            # MetaPlanner: long template ending in `Meta Plan:`. Return any
            # short META PLAN text that is *not* literal "Query not relevant"
            # and does not include WORKSPACE_TOPOLOGY (so we exercise the
            # single-workspace path).
            return "META PLAN:\\nQUERY UNDERSTANDING: stub.\\nRESEARCH APPROACH: just call stub_agent.\\n"


    # ---- entry points required by the CLI -------------------------
    def get_llm():
        return _StubLLM()


    def get_tools():
        return [{
            "class": "_StubAgentTool",
            "name": "stub_agent",
            "extra_info": "stub coding agent for cli_smoke",
        }]
    '''
)


def _run_cli(args, env=None, check=True, capture=True):
    """Invoke the CLI as a subprocess via `python -m rizz`."""
    proc_env = os.environ.copy()
    proc_env["PYTHONPATH"] = str(REPO_ROOT) + (
        os.pathsep + proc_env.get("PYTHONPATH", "")
        if proc_env.get("PYTHONPATH") else ""
    )
    if env:
        proc_env.update(env)
    res = subprocess.run(
        [sys.executable, "-m", "rizz", *args],
        env=proc_env,
        cwd=str(REPO_ROOT),
        capture_output=capture,
        text=True,
        check=False,
    )
    if check and res.returncode != 0:
        sys.stderr.write(f"CLI failed: args={args}\nrc={res.returncode}\n")
        sys.stderr.write(f"stdout:\n{res.stdout}\n")
        sys.stderr.write(f"stderr:\n{res.stderr}\n")
        raise RuntimeError("CLI exited non-zero")
    return res


def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="rizz-cli-smoke-"))
    repo = tmp / "repo"
    repo.mkdir()
    _init_test_repo(repo)

    cfg_path = tmp / "stub_tools.py"
    cfg_path.write_text(TOOLS_CONFIG_SRC)
    # Drop the stub class file next to the cfg so tool_generator can find it
    # (it scans the directory containing tool_path).
    (tmp / "stub_tools_companion.py").write_text(
        "# placeholder; the stub tool class lives in stub_tools.py and is "
        "# auto-discovered there because tool_path points at this directory."
    )

    try:
        # 1. `rizz run`
        res = _run_cli(
            [
                "run",
                "--repo", str(repo),
                "--question", "do the thing",
                "--tools-config", str(cfg_path),
                "--no-multi-workspace",
                "--cleanup", "never",
                "--no-open-pr",
            ],
        )
        assert "run_id:" in res.stdout, f"run output: {res.stdout!r}"
        run_id = None
        for line in res.stdout.splitlines():
            if line.startswith("run_id:"):
                run_id = line.split(":", 1)[1].strip()
                break
        assert run_id and run_id.startswith("run-"), f"bad run_id: {run_id!r}"
        print(f"rizz run: created {run_id}")

        # 2. `rizz list`
        res = _run_cli(["list", "--repo", str(repo)])
        assert run_id in res.stdout, f"list output missing run_id:\n{res.stdout}"
        assert "completed" in res.stdout
        print("rizz list: OK")

        # 3. `rizz status <run-id>` (text)
        res = _run_cli(["status", "--repo", str(repo), run_id])
        assert f"Run {run_id}" in res.stdout
        assert "Status:      completed" in res.stdout
        assert "Workspaces" in res.stdout
        assert "Tasks" in res.stdout
        print("rizz status: OK")

        # 3b. `rizz status --json`
        res = _run_cli(["status", "--repo", str(repo), run_id, "--json"])
        payload = json.loads(res.stdout)
        assert payload["summary"]["run_id"] == run_id
        assert payload["summary"]["status"] == "completed"
        print("rizz status --json: OK")

        # 4. `rizz prs <run-id>` (empty list, exit 0)
        res = _run_cli(["prs", "--repo", str(repo), run_id])
        assert res.stdout.strip() == "", f"expected empty prs output, got {res.stdout!r}"
        assert res.returncode == 0
        print("rizz prs (empty): OK")

        # 5. `rizz clean --dry-run`
        res = _run_cli(
            ["clean", "--repo", str(repo), "--keep-recent", "0", "--dry-run"]
        )
        assert "would delete 1" in res.stdout, res.stdout
        assert "(--dry-run: no changes made)" in res.stdout
        # Run still present after dry-run.
        res = _run_cli(["list", "--repo", str(repo)])
        assert run_id in res.stdout
        print("rizz clean --dry-run: OK")

        # 6. `rizz clean` for real.
        _run_cli(["clean", "--repo", str(repo), "--keep-recent", "0"])
        res = _run_cli(["list", "--repo", str(repo)])
        assert run_id not in res.stdout, f"run survived clean:\n{res.stdout}"
        print("rizz clean: OK")

        # 7. Status of deleted run -> exit 2
        res = _run_cli(
            ["status", "--repo", str(repo), run_id], check=False
        )
        assert res.returncode == 2, f"expected exit 2, got {res.returncode}"
        print("rizz status of deleted run: exit 2 OK")

        print("cli_smoke: PASS")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    main()
