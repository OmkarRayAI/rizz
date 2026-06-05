"""Phase 3 — async wrappers around the GitHub `gh` CLI.

Mirrors the `git_utils.run_git` chokepoint pattern. We never manage
`gh` credentials ourselves; we trust `gh auth login` (or `GH_TOKEN`).
If `gh` is missing, callers get a clear error rather than silent
fallback — auto-PR is opt-in, so silent failure would hide that the
feature isn't actually working.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import Optional


class GhError(RuntimeError):
    def __init__(self, cmd: list[str], code: int, stdout: str, stderr: str):
        self.cmd = cmd
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"gh {' '.join(cmd)} failed (exit {code}): "
            f"{stderr.strip() or stdout.strip()}"
        )


async def is_gh_available() -> bool:
    return shutil.which("gh") is not None


async def _run_gh(args: list[str], cwd: str | Path) -> tuple[int, str, str]:
    full_env = os.environ.copy()
    full_env.setdefault("GH_PROMPT_DISABLED", "1")
    proc = await asyncio.create_subprocess_exec(
        "gh",
        *args,
        cwd=str(cwd),
        env=full_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out_b, err_b = await proc.communicate()
    out = out_b.decode("utf-8", errors="replace")
    err = err_b.decode("utf-8", errors="replace")
    return (proc.returncode if proc.returncode is not None else -1), out, err


async def gh_pr_create(
    repo_dir: Path,
    *,
    base: str,
    head: str,
    title: str,
    body: str,
    draft: bool = False,
) -> str:
    """Create a PR via `gh pr create`. Returns the PR URL.

    Pre-conditions: the head branch must already be pushed to the
    remote. Caller is responsible for that (we keep `push` and
    `pr_create` separable so push failures are diagnosed directly).
    """
    if not await is_gh_available():
        raise GhError(
            ["pr", "create"], 127, "", "`gh` CLI not found on PATH"
        )
    args = [
        "pr",
        "create",
        "--base",
        base,
        "--head",
        head,
        "--title",
        title,
        "--body",
        body,
    ]
    if draft:
        args.append("--draft")
    code, out, err = await _run_gh(args, cwd=repo_dir)
    if code != 0:
        raise GhError(args, code, out, err)
    # `gh pr create` prints the PR URL on stdout (last non-empty line).
    lines = [line for line in out.strip().splitlines() if line.strip()]
    return lines[-1] if lines else ""
