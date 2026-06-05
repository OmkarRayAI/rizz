"""WorkspaceManager — Phase 1.

Owns ID allocation, git-worktree creation, registry of live workspaces,
and cleanup. Lifetime is one `Rizz.run()` call.

Stale-detection: on init, dangling dirs under the worktree root that aren't
in `git worktree list` are *logged*, never auto-pruned. Auto-prune across
processes is a Phase-4 concern (could nuke another Rizz session's work).
"""

from __future__ import annotations

import asyncio
import logging
import secrets
from pathlib import Path
from typing import Optional

from . import git_utils
from .constants import (
    RIZZ_BRANCH_PREFIX,
    RIZZ_WORKTREE_DIR_NAME,
    RIZZ_WORKTREE_SUBDIR,
)
from .workspace import Workspace, create_degraded_workspace, create_git_workspace

log = logging.getLogger(__name__)


class WorkspaceManager:
    def __init__(self, *, repo: Optional[Path], root: Optional[Path] = None):
        self._repo = Path(repo).resolve() if repo else None
        if root is not None:
            self._root = Path(root).resolve()
        elif self._repo is not None:
            self._root = (
                self._repo / RIZZ_WORKTREE_DIR_NAME / RIZZ_WORKTREE_SUBDIR
            )
        else:
            self._root = None
        self._registry: dict[Path, Workspace] = {}
        self._lock = asyncio.Lock()
        if self._root is not None and self._root.exists():
            try:
                stale = self._sync_detect_stale()
                if stale:
                    log.info(
                        "found %d possibly-stale worktree dir(s) under %s; "
                        "run `git worktree prune` if you're sure no other process owns them",
                        len(stale),
                        self._root,
                    )
            except Exception as e:
                log.debug("stale detection skipped: %s", e)

    @property
    def repo(self) -> Optional[Path]:
        return self._repo

    @property
    def root(self) -> Optional[Path]:
        return self._root

    async def allocate(
        self,
        *,
        base_ref: Optional[str] = None,
        branch_hint: Optional[str] = None,
        env_vars: Optional[dict[str, str]] = None,
    ) -> Workspace:
        async with self._lock:
            wsid = await self._mint_id()
            if self._repo is None:
                ws = await create_degraded_workspace(
                    manager=self, wsid=wsid, env_vars=env_vars
                )
                self._registry[ws.cwd.resolve()] = ws
                return ws

            await self._preflight_repo()
            actual_base = base_ref or await git_utils.head_sha(self._repo)
            branch = self._make_branch_name(wsid, branch_hint)
            assert self._root is not None
            self._root.mkdir(parents=True, exist_ok=True)
            cwd = (self._root / wsid).resolve()
            if cwd in self._registry:
                raise RuntimeError(f"workspace path already registered: {cwd}")

            await git_utils.worktree_add(
                self._repo, cwd, branch=branch, base_ref=actual_base
            )
            ws = await create_git_workspace(
                manager=self,
                repo=self._repo,
                cwd=cwd,
                branch=branch,
                base_ref=actual_base,
                env_vars=env_vars,
                wsid=wsid,
            )
            self._registry[cwd] = ws
            return ws

    async def cleanup_all(
        self,
        *,
        force: bool = True,
        policy: str = "force",
        run_status: Optional[str] = None,
    ) -> None:
        """Tear down every workspace this manager owns.

        Phase 4 added `policy` and `run_status`:

        - ``policy="force"`` (default; matches Phase 1-3) → always cleanup.
        - ``policy="skip"`` → leave worktrees and branches in place. The
          caller has decided to keep state for inspection / resume.
        - ``policy="on_success"`` → cleanup only when ``run_status == "completed"``;
          otherwise leave state for post-mortem.

        ``force`` is forwarded to ``Workspace.cleanup``; controls whether
        ``git worktree remove --force`` is used. Defaults to True so a
        dirty worktree doesn't block cleanup.
        """
        if policy == "skip":
            return
        if policy == "on_success" and run_status != "completed":
            return
        # Snapshot — workspace.cleanup mutates the registry.
        live = list(self._registry.values())
        for ws in live:
            try:
                await ws.cleanup(force=force)
            except Exception as e:
                log.warning("cleanup failed for workspace %s: %s", ws.id, e)

    def _unregister(self, ws: Workspace) -> None:
        self._registry.pop(ws.cwd.resolve(), None)

    async def _preflight_repo(self) -> None:
        assert self._repo is not None
        root = await git_utils.find_repo_root(self._repo)
        if root is None:
            raise RuntimeError(f"{self._repo} is not inside a git repository")
        if await git_utils.is_bare(root):
            raise RuntimeError(f"{root} is a bare repository; cannot add a worktree")
        if await git_utils.is_shallow(root):
            log.warning(
                "%s is a shallow clone; worktree add may behave unexpectedly", root
            )

    async def _mint_id(self) -> str:
        for _ in range(16):
            wsid = "wk-" + secrets.token_hex(3)
            cwd = (
                (self._root / wsid).resolve()
                if self._root is not None
                else Path(f"/tmp/rizz-{wsid}")
            )
            if cwd in self._registry:
                continue
            if self._repo is None:
                return wsid
            entries = await git_utils.worktree_list(self._repo)
            if not any(Path(e.get("worktree", "")).resolve() == cwd for e in entries):
                return wsid
        raise RuntimeError("failed to mint a unique workspace id after 16 tries")

    def _make_branch_name(self, wsid: str, hint: Optional[str]) -> str:
        suffix = wsid
        if hint:
            cleaned = "".join(c for c in hint if c.isalnum() or c in "-_")[:24]
            if cleaned:
                suffix = f"{cleaned}-{wsid}"
        return f"{RIZZ_BRANCH_PREFIX}{suffix}"

    def _sync_detect_stale(self) -> list[Path]:
        """Best-effort *synchronous* stale detection at __init__ time.

        We can't await here, so we just enumerate dirs without consulting git.
        Phase 1 only logs the count; the path list is informational.
        """
        if self._root is None or not self._root.exists():
            return []
        return [p for p in self._root.iterdir() if p.is_dir()]

    async def detect_stale(self) -> list[Path]:
        if self._root is None or not self._root.exists():
            return []
        on_disk = {p.resolve() for p in self._root.iterdir() if p.is_dir()}
        if self._repo is None:
            return sorted(on_disk)
        entries = await git_utils.worktree_list(self._repo)
        registered = {
            Path(e["worktree"]).resolve() for e in entries if e.get("worktree")
        }
        return sorted(on_disk - registered)
