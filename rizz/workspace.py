"""The `Workspace` primitive — Phase 1.

A workspace represents one isolated working directory + branch + notes folder
that a Task can mutate without disturbing the host repo or other tasks. Phase 1
is opt-in; tools that don't declare a `workspace` parameter are unaffected.

Layout: `<repo>/.rizz/worktrees/<id>/` (gitignored), with a sibling
`<cwd>/.rizz/notes/` for the seed of Conductor's `.context/` notion. We
deliberately avoid the key name `context` to prevent collision with the
replanner's `inputs["context"]` string in `llm_compiler.py`.

Degraded mode: if the caller didn't pass `repo=`, we still hand out a
`Workspace` whose `cwd` is the host cwd and `branch is None`. Tools needing
a real branch should check `ws.branch is not None`. Hard error only if the
caller explicitly asked for a repo and the path isn't one (or is bare).
"""

from __future__ import annotations

import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from . import git_utils

if TYPE_CHECKING:
    from .workspace_manager import WorkspaceManager

log = logging.getLogger(__name__)

_NOTE_NAME_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _sanitize_note_name(name: str) -> str:
    if not _NOTE_NAME_RE.match(name) or name in (".", ".."):
        raise ValueError(
            f"note name {name!r} must match [A-Za-z0-9._-]+ and not be . or .."
        )
    return name


@dataclass
class Workspace:
    id: str
    cwd: Path
    branch: Optional[str]
    base_ref: Optional[str]
    notes_dir: Path
    repo_root: Optional[Path]
    env_vars: dict[str, str] = field(default_factory=dict)
    _owns_worktree: bool = False
    _owns_notes_tempdir: bool = False
    _manager: Optional["WorkspaceManager"] = None
    _cleaned: bool = False

    @classmethod
    async def create(
        cls,
        *,
        manager: "WorkspaceManager",
        repo: Optional[Path],
        cwd: Path,
        branch: Optional[str],
        base_ref: Optional[str],
        env_vars: Optional[dict[str, str]] = None,
        owns_worktree: bool,
        owns_notes_tempdir: bool,
        notes_dir: Path,
        wsid: str,
    ) -> "Workspace":
        notes_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            id=wsid,
            cwd=cwd,
            branch=branch,
            base_ref=base_ref,
            notes_dir=notes_dir,
            repo_root=repo,
            env_vars=dict(env_vars or {}),
            _owns_worktree=owns_worktree,
            _owns_notes_tempdir=owns_notes_tempdir,
            _manager=manager,
        )

    async def cleanup(self, *, force: bool = True) -> None:
        if self._cleaned:
            return
        self._cleaned = True
        try:
            if self._owns_worktree and self.repo_root is not None:
                try:
                    await git_utils.worktree_remove(
                        self.repo_root, self.cwd, force=force
                    )
                except git_utils.GitError as e:
                    log.warning("worktree remove failed for %s: %s", self.cwd, e)
                if self.branch:
                    try:
                        await git_utils.branch_delete(
                            self.repo_root, self.branch, force=True
                        )
                    except git_utils.GitError as e:
                        log.info(
                            "leaving branch %s on disk (had commits or remove failed): %s",
                            self.branch,
                            e,
                        )
            if self._owns_notes_tempdir:
                import shutil

                shutil.rmtree(self.notes_dir.parent, ignore_errors=True)
        finally:
            if self._manager is not None:
                self._manager._unregister(self)

    async def write_note(self, name: str, content: str) -> Path:
        safe = _sanitize_note_name(name)
        path = self.notes_dir / safe
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    async def read_note(self, name: str) -> Optional[str]:
        safe = _sanitize_note_name(name)
        path = self.notes_dir / safe
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    async def diff(self, ref: Optional[str] = None) -> str:
        if self.repo_root is None:
            return ""
        return await git_utils.diff(self.cwd, ref or self.base_ref)

    async def status(self) -> str:
        if self.repo_root is None:
            return ""
        return await git_utils.status(self.cwd)

    async def run_git(self, *args: str) -> str:
        if self.repo_root is None:
            raise RuntimeError(
                "Workspace is in degraded (no-repo) mode; git operations are unavailable"
            )
        _, out, _ = await git_utils.run_git(list(args), cwd=self.cwd)
        return out


async def create_degraded_workspace(
    *, manager: "WorkspaceManager", wsid: str, env_vars: Optional[dict[str, str]] = None
) -> Workspace:
    """Build a Workspace with no git backing — just a tempdir for notes."""
    tmp = Path(tempfile.mkdtemp(prefix=f"rizz-{wsid}-"))
    notes = tmp / "notes"
    return await Workspace.create(
        manager=manager,
        repo=None,
        cwd=Path.cwd(),
        branch=None,
        base_ref=None,
        env_vars=env_vars,
        owns_worktree=False,
        owns_notes_tempdir=True,
        notes_dir=notes,
        wsid=wsid,
    )


async def create_git_workspace(
    *,
    manager: "WorkspaceManager",
    repo: Path,
    cwd: Path,
    branch: str,
    base_ref: str,
    env_vars: Optional[dict[str, str]] = None,
    wsid: str,
) -> Workspace:
    notes = cwd / ".rizz" / "notes"
    return await Workspace.create(
        manager=manager,
        repo=repo,
        cwd=cwd,
        branch=branch,
        base_ref=base_ref,
        env_vars=env_vars,
        owns_worktree=True,
        owns_notes_tempdir=False,
        notes_dir=notes,
        wsid=wsid,
    )
