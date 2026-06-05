"""Phase 2 — structured observation for coding agents.

The joiner only ever sees `__str__`, so the format below is the entire
joiner-visible contract. Downstream tasks can reach individual fields via
`$1.summary` / `$1.branch` / etc. through the regex sub in
`task_fetching_unit._replace_arg_mask_with_real_value`.

Note on `base_ref` / cumulative diff: Phase 1 keeps `Workspace.base_ref` as
the worktree's *fork point* (it never moves). Successive coding-agent tasks
in the same DAG see *cumulative* diffs against that fork point. Per-agent
attribution is preserved through the `commits` field, which holds only the
SHAs that **this** agent created. Downstream callers needing a per-agent
diff can reconstruct it via `git diff <commit>^!`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from ..workspace import Workspace


# Render-time bounds — the joiner sees this; keep its scratchpad sane.
_DIFF_LINE_LIMIT = 200
_DIFF_CHAR_LIMIT = 8_000
_SUMMARY_MAX_LEN = 500


@dataclass
class AgentResult:
    summary: str = ""
    diff: str = ""
    files_changed: List[str] = field(default_factory=list)
    branch: Optional[str] = None
    commits: List[str] = field(default_factory=list)
    exit_status: str = "ok"  # "ok" | "no_changes" | "error" | "timeout"
    transcript: Optional[str] = None  # opt-in; not rendered in __str__
    error: Optional[str] = None

    @property
    def diff_truncated(self) -> str:
        if not self.diff:
            return ""
        lines = self.diff.splitlines()
        body = "\n".join(lines[:_DIFF_LINE_LIMIT])
        if len(lines) > _DIFF_LINE_LIMIT:
            body += f"\n... [{len(lines) - _DIFF_LINE_LIMIT} more lines truncated]"
        if len(body) > _DIFF_CHAR_LIMIT:
            body = body[:_DIFF_CHAR_LIMIT] + "\n... [diff truncated to char limit]"
        return body

    def __str__(self) -> str:
        head = f"[{self.exit_status}] {self.summary[:_SUMMARY_MAX_LEN]}".rstrip()
        meta_bits: List[str] = []
        if self.branch:
            meta_bits.append(f"branch={self.branch}")
        if self.commits:
            meta_bits.append("commits=" + ",".join(c[:8] for c in self.commits))
        if self.files_changed:
            meta_bits.append(f"files={len(self.files_changed)}")
        meta_line = " ".join(meta_bits)

        diff_block = self.diff_truncated
        parts = [head]
        if meta_line:
            parts.append(meta_line)
        if diff_block:
            parts.append(f"--- diff ---\n{diff_block}")
        return "\n".join(parts)

    @classmethod
    async def from_workspace(
        cls,
        workspace: "Workspace",
        *,
        summary: str,
        new_commits: Optional[List[str]] = None,
        transcript: Optional[str] = None,
        exit_status: str = "ok",
        error: Optional[str] = None,
    ) -> "AgentResult":
        """Snapshot a workspace into an AgentResult.

        `diff` is computed against `workspace.base_ref` (the worktree's fork
        point). `files_changed` comes from `git status --porcelain` and so
        captures both committed and uncommitted changes since the fork.
        """
        diff_text = ""
        files: List[str] = []
        if workspace.repo_root is not None:
            try:
                diff_text = await workspace.diff()
            except Exception:
                diff_text = ""
            try:
                status_text = await workspace.status()
                seen = set()
                for line in status_text.splitlines():
                    if len(line) <= 3:
                        continue
                    name = line[3:].strip()
                    if name and name not in seen:
                        seen.add(name)
                        files.append(name)
                files.sort()
            except Exception:
                files = []

        return cls(
            summary=summary,
            diff=diff_text,
            files_changed=files,
            branch=workspace.branch,
            commits=list(new_commits or []),
            transcript=transcript,
            exit_status=exit_status,
            error=error,
        )
