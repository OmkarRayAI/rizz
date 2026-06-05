"""Phase 1 D.2 — Workspace lifecycle smoke test.

Initializes a throwaway git repo in a tempdir, allocates a Workspace,
writes/reads a note, runs status/diff, and verifies cleanup.

Run: python examples/workspace_smoke.py
"""

import asyncio
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# Allow running from the repo root without installing
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

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


async def main() -> None:
    tmp = Path(tempfile.mkdtemp(prefix="rizz-smoke-"))
    repo = tmp / "repo"
    repo.mkdir()
    _init_test_repo(repo)
    try:
        mgr = WorkspaceManager(repo=repo)
        ws = await mgr.allocate()
        try:
            assert ws.cwd.exists() and ws.cwd != repo, "worktree should be a sibling"
            assert ws.branch and ws.branch.startswith("rizz/"), \
                f"unexpected branch {ws.branch!r}"
            print(f"allocated {ws.id} at {ws.cwd} on branch {ws.branch}")

            await ws.write_note("hello.md", "first note\n")
            assert (await ws.read_note("hello.md")) == "first note\n"
            print("note round-trip OK")

            st = await ws.status()
            print(f"status: {st!r}")
            d = await ws.diff()
            print(f"diff bytes: {len(d)}")

            # name sanitization
            try:
                await ws.write_note("../escape.md", "x")
                raise AssertionError("expected ValueError on path-traversal note name")
            except ValueError:
                print("note name sanitization OK")
        finally:
            await mgr.cleanup_all()
        assert not ws.cwd.exists(), f"worktree {ws.cwd} survived cleanup"
        print("cleanup OK")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("workspace_smoke: PASS")


if __name__ == "__main__":
    asyncio.run(main())
