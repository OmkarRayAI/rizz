"""Async wrappers around the `git` CLI.

Single chokepoint: every helper goes through `run_git`, which uses
`asyncio.create_subprocess_exec` so it never blocks the event loop driving
the TaskFetchingUnit. Output is locale-stable (`LC_ALL=C`) and we disable
credential prompts (`GIT_TERMINAL_PROMPT=0`) so a missing credential helper
never hangs an automated run.

Failure mode: every helper except `find_repo_root` raises `GitError` on
non-zero exit. `find_repo_root` returns `Optional[Path]` so the Workspace
layer can branch on "not in a repo" without try/except plumbing.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Optional


class GitError(RuntimeError):
    def __init__(self, cmd: list[str], code: int, stdout: str, stderr: str):
        self.cmd = cmd
        self.code = code
        self.stdout = stdout
        self.stderr = stderr
        super().__init__(
            f"git {' '.join(cmd)} failed (exit {code}): {stderr.strip() or stdout.strip()}"
        )


async def run_git(
    args: list[str],
    cwd: str | Path,
    env: Optional[dict[str, str]] = None,
    check: bool = True,
) -> tuple[int, str, str]:
    full_env = os.environ.copy()
    full_env["LC_ALL"] = "C"
    full_env["GIT_TERMINAL_PROMPT"] = "0"
    if env:
        full_env.update(env)

    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd),
        env=full_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await proc.communicate()
    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    code = proc.returncode if proc.returncode is not None else -1
    if check and code != 0:
        raise GitError(args, code, stdout, stderr)
    return code, stdout, stderr


async def find_repo_root(start: str | Path) -> Optional[Path]:
    code, out, _ = await run_git(
        ["rev-parse", "--show-toplevel"], cwd=start, check=False
    )
    if code != 0:
        return None
    root = out.strip()
    return Path(root) if root else None


async def current_branch(repo: str | Path) -> Optional[str]:
    code, out, _ = await run_git(
        ["symbolic-ref", "--short", "HEAD"], cwd=repo, check=False
    )
    if code != 0:
        return None
    return out.strip() or None


async def head_sha(repo: str | Path) -> str:
    _, out, _ = await run_git(["rev-parse", "HEAD"], cwd=repo)
    return out.strip()


async def is_bare(repo: str | Path) -> bool:
    _, out, _ = await run_git(["rev-parse", "--is-bare-repository"], cwd=repo)
    return out.strip() == "true"


async def is_shallow(repo: str | Path) -> bool:
    _, out, _ = await run_git(["rev-parse", "--is-shallow-repository"], cwd=repo)
    return out.strip() == "true"


async def is_dirty(repo: str | Path) -> bool:
    _, out, _ = await run_git(["status", "--porcelain"], cwd=repo)
    return bool(out.strip())


async def worktree_add(
    repo: str | Path,
    path: str | Path,
    branch: str,
    base_ref: str,
    create_branch: bool = True,
) -> None:
    args = ["worktree", "add"]
    if create_branch:
        args += ["-b", branch]
    args += [str(path), base_ref]
    await run_git(args, cwd=repo)


async def worktree_remove(
    repo: str | Path, path: str | Path, force: bool = False
) -> None:
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(path))
    await run_git(args, cwd=repo)


async def worktree_list(repo: str | Path) -> list[dict]:
    """Parse `git worktree list --porcelain` into a list of dicts."""
    _, out, _ = await run_git(["worktree", "list", "--porcelain"], cwd=repo)
    entries: list[dict] = []
    cur: dict = {}
    for line in out.splitlines():
        if not line.strip():
            if cur:
                entries.append(cur)
                cur = {}
            continue
        if " " in line:
            key, _, val = line.partition(" ")
        else:
            key, val = line, ""
        cur[key] = val
    if cur:
        entries.append(cur)
    return entries


async def diff(
    repo: str | Path, ref: Optional[str] = None, include_untracked: bool = True
) -> str:
    args = ["diff"]
    if ref:
        args.append(ref)
    _, tracked, _ = await run_git(args, cwd=repo)

    if not include_untracked:
        return tracked

    _, listing, _ = await run_git(
        ["ls-files", "--others", "--exclude-standard"], cwd=repo
    )
    untracked_files = [p for p in listing.splitlines() if p.strip()]
    if not untracked_files:
        return tracked

    chunks = [tracked] if tracked else []
    for rel in untracked_files:
        full = Path(repo) / rel
        try:
            content = full.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            chunks.append(f"=== untracked binary: {rel} ===\n")
            continue
        chunks.append(f"=== untracked: {rel} ===\n{content}")
    return "\n".join(chunks)


async def status(repo: str | Path, porcelain: bool = True) -> str:
    args = ["status"]
    if porcelain:
        args.append("--porcelain")
    _, out, _ = await run_git(args, cwd=repo)
    return out


async def branch_delete(repo: str | Path, branch: str, force: bool = False) -> None:
    args = ["branch", "-D" if force else "-d", branch]
    await run_git(args, cwd=repo)


# --- Phase 3 ----------------------------------------------------------


async def merge_tree(
    repo: str | Path, base_a: str, base_b: str
) -> tuple[bool, list[str]]:
    """Pairwise conflict detection via `git merge-tree --write-tree --name-only`.

    Returns `(clean, conflicting_paths)`. Clean merges return
    `(True, [])`. Conflicting merges return `(False, [<paths>])`.

    The conflict-mode output format is:

        <merged-tree-sha>
        <conflicting path 1>
        <conflicting path 2>
                                          <- blank line separator
        Auto-merging <file>               <- informational lines (skipped)
        CONFLICT (content): Merge conflict in <file>
        ...

    We keep only the path section before the first blank line, after
    discarding the leading SHA.

    Requires git ≥ 2.38 (the `--write-tree --name-only` mode). Older
    gits raise `GitError`; the caller is expected to log and proceed
    without a conflict report.
    """
    code, out, _ = await run_git(
        ["merge-tree", "--write-tree", "--name-only", base_a, base_b],
        cwd=repo,
        check=False,
    )
    if code == 0:
        return True, []

    files: list[str] = []
    saw_sha = False
    for raw in out.splitlines():
        line = raw.rstrip("\n")
        # First non-empty line is the merged-tree SHA — discard it.
        if not saw_sha:
            if line.strip():
                saw_sha = True
            continue
        # Blank line ends the file-paths section.
        if not line.strip():
            break
        files.append(line.strip())
    return False, files


async def push(
    repo_dir: str | Path,
    branch: str,
    remote: str = "origin",
    set_upstream: bool = True,
) -> None:
    args = ["push"]
    if set_upstream:
        args.append("-u")
    args += [remote, branch]
    await run_git(args, cwd=repo_dir)


async def get_remote_url(
    repo: str | Path, name: str = "origin"
) -> Optional[str]:
    code, out, _ = await run_git(
        ["remote", "get-url", name], cwd=repo, check=False
    )
    if code != 0:
        return None
    return out.strip() or None


async def get_default_branch(
    repo: str | Path, remote: str = "origin"
) -> Optional[str]:
    """Detect the default branch of a remote (e.g. `main`, `master`).

    Reads `refs/remotes/<remote>/HEAD` via `git symbolic-ref`. Returns
    None if the remote/HEAD ref isn't present (e.g. remote was added
    but never fetched).
    """
    code, out, _ = await run_git(
        ["symbolic-ref", f"refs/remotes/{remote}/HEAD"],
        cwd=repo,
        check=False,
    )
    if code != 0:
        return None
    val = out.strip()
    prefix = f"refs/remotes/{remote}/"
    return val[len(prefix):] if val.startswith(prefix) else None
