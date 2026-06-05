"""Phase 4 — frozen dataclass records returned by `RunStore` reads.

These are *value* types: pure data, no methods that touch the DB. The
store hands them back; the CLI renders them; tests inspect them. They
shadow the `Workspace` / `Task` / `AgentResult` runtime classes but are
detached from any live object — safe to serialize, persist, ship over
a wire.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional


@dataclass(frozen=True)
class RunSummary:
    run_id: str
    repo: str
    question: str
    status: str  # 'running' | 'completed' | 'failed'
    started_at: str
    finished_at: Optional[str]
    num_workspaces: int
    num_prs: int


@dataclass(frozen=True)
class WorkspaceRecord:
    workspace_id: str
    run_id: str
    group_name: Optional[str]
    branch: Optional[str]
    base_ref: Optional[str]
    cwd: str
    owns_worktree: bool
    created_at: str


@dataclass(frozen=True)
class TaskRecord:
    run_id: str
    idx: int
    name: str
    args: List[Any] = field(default_factory=list)
    dependencies: List[int] = field(default_factory=list)
    workspace_group: Optional[str] = None
    is_join: bool = False


@dataclass(frozen=True)
class AgentResultRecord:
    run_id: str
    task_idx: int
    summary: str = ""
    diff: str = ""
    files_changed: List[str] = field(default_factory=list)
    branch: Optional[str] = None
    commits: List[str] = field(default_factory=list)
    exit_status: str = "ok"
    error: Optional[str] = None
    transcript: Optional[str] = None
    finished_at: Optional[str] = None


@dataclass(frozen=True)
class PrLinkRecord:
    run_id: str
    group_name: str  # '__single__' for non-multi runs
    url: str
    created_at: str


@dataclass(frozen=True)
class RunRecord:
    summary: RunSummary
    purpose: Optional[str]
    multi_workspace: bool
    open_pr: bool
    cleanup_policy: str
    raw_answer: Optional[str]
    thinking_process: Optional[str]
    error: Optional[str]
    workspaces: List[WorkspaceRecord] = field(default_factory=list)
    tasks: List[TaskRecord] = field(default_factory=list)
    results: List[AgentResultRecord] = field(default_factory=list)
    prs: List[PrLinkRecord] = field(default_factory=list)
