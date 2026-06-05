"""Phase 2 E.2 — Real Claude Agent SDK example (gated).

Skips with a friendly message if `ANTHROPIC_API_KEY` is unset or
`claude-agent-sdk` isn't installed. Documentation, not CI.

Run: ANTHROPIC_API_KEY=... python examples/agent_e2e_claude.py
"""

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _init_test_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "test"], check=True
    )
    (root / "README.md").write_text("# test repo\n")
    (root / "main.py").write_text(
        "def add(a, b):\n    return a + b\n\nif __name__ == '__main__':\n"
        "    print(add(1, 2))\n"
    )
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "-m", "init"], check=True
    )


async def main() -> None:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY not set; skipping. (This example is gated.)")
        return
    try:
        import claude_agent_sdk  # noqa: F401
    except ImportError:
        print(
            "claude-agent-sdk not installed; skipping. "
            "Run: pip install claude-agent-sdk"
        )
        return

    from rizz.agents.claude_code import ClaudeCodeAgent
    from rizz.workspace_manager import WorkspaceManager

    tmp = Path(tempfile.mkdtemp(prefix="rizz-agent-e2e-"))
    repo = tmp / "repo"
    repo.mkdir()
    _init_test_repo(repo)
    try:
        mgr = WorkspaceManager(repo=repo)
        ws = await mgr.allocate()
        try:
            agent = ClaudeCodeAgent(
                name="docstring_adder",
                description="Adds a one-line docstring to the `add` function.",
                permission_mode="acceptEdits",
                timeout_seconds=180,
                capture_transcript=True,
            )
            result = await agent.run(
                goal=(
                    "Open main.py and add a one-line docstring to the `add` "
                    "function describing what it does. Keep edits minimal."
                ),
                workspace=ws,
            )
            print("=" * 60)
            print("AgentResult:")
            print(result)
            print("=" * 60)
            print(f"branch: {result.branch}")
            print(f"commits: {result.commits}")
            print(f"files_changed: {result.files_changed}")
            print(f"exit_status: {result.exit_status}")
            if result.error:
                print(f"error: {result.error}")
        finally:
            await mgr.cleanup_all()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
