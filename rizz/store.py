"""Phase 4 — SQLite-backed RunStore.

Persistence chokepoint. Every `Rizz.run()` writes its run, workspaces,
tasks, observations, and PR links here. The DB lives at
`<repo>/.rizz/state.db`, co-located with the worktrees so cleanup is
symmetric.

Storage decisions:
- stdlib `sqlite3` only — no new dependencies for v1.
- WAL journal mode so readers don't block writers.
- `BEGIN IMMEDIATE` for writes; `busy_timeout=5000` for cross-process safety.
- All writes wrap in a `threading.Lock` to serialize coroutine writes
  through one connection.
- Heavy ops (`vacuum`) go through `asyncio.to_thread`; everything else is
  inline because each statement is microseconds.

Schema versioning:
- `_SCHEMA_VERSION = 1` lives alongside `_SCHEMA_V1_STATEMENTS`.
- v2 will add `_SCHEMA_V2_STATEMENTS` and `_migrate_to_v2(conn)`. We do
  NOT build a generic migration framework here.

Privacy:
- `agent_results.diff` and `agent_results.transcript` may contain code or
  secrets. The DB is gitignored by Phase 1's repo-level `.gitignore`. No
  redaction in Phase 4; that's a Phase 5+ concern.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Optional

from .constants import RIZZ_WORKTREE_DIR_NAME
from .run_record import (
    AgentResultRecord,
    PrLinkRecord,
    RunRecord,
    RunSummary,
    TaskRecord,
    WorkspaceRecord,
)

if TYPE_CHECKING:
    from .task_fetching_unit import Task
    from .workspace import Workspace

log = logging.getLogger(__name__)

_SCHEMA_VERSION = 1
_DEFAULT_DB_NAME = "state.db"

_PR_SINGLE_SENTINEL = "__single__"

# --- Schema v1 ----------------------------------------------------------

_SCHEMA_V1_STATEMENTS: List[str] = [
    """
    CREATE TABLE IF NOT EXISTS schema_version (
        version INTEGER PRIMARY KEY
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS runs (
        run_id           TEXT PRIMARY KEY,
        repo             TEXT NOT NULL,
        question         TEXT NOT NULL,
        purpose          TEXT,
        status           TEXT NOT NULL,
        started_at       TEXT NOT NULL,
        finished_at      TEXT,
        multi_workspace  INTEGER NOT NULL DEFAULT 1,
        open_pr          INTEGER NOT NULL DEFAULT 0,
        cleanup_policy   TEXT NOT NULL DEFAULT 'auto',
        raw_answer       TEXT,
        thinking_process TEXT,
        error            TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_runs_repo ON runs(repo)",
    "CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)",
    "CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at DESC)",
    """
    CREATE TABLE IF NOT EXISTS workspaces (
        workspace_id  TEXT NOT NULL,
        run_id        TEXT NOT NULL,
        group_name    TEXT,
        branch        TEXT,
        base_ref      TEXT,
        cwd           TEXT NOT NULL,
        owns_worktree INTEGER NOT NULL DEFAULT 1,
        created_at    TEXT NOT NULL,
        PRIMARY KEY (run_id, workspace_id),
        FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_ws_run ON workspaces(run_id)",
    """
    CREATE TABLE IF NOT EXISTS tasks (
        run_id          TEXT NOT NULL,
        idx             INTEGER NOT NULL,
        name            TEXT NOT NULL,
        args            TEXT,
        dependencies    TEXT,
        workspace_group TEXT,
        is_join         INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (run_id, idx),
        FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id)",
    """
    CREATE TABLE IF NOT EXISTS agent_results (
        run_id        TEXT NOT NULL,
        task_idx      INTEGER NOT NULL,
        summary       TEXT,
        diff          TEXT,
        files_changed TEXT,
        branch        TEXT,
        commits       TEXT,
        exit_status   TEXT,
        error         TEXT,
        transcript    TEXT,
        finished_at   TEXT,
        PRIMARY KEY (run_id, task_idx),
        FOREIGN KEY (run_id, task_idx) REFERENCES tasks(run_id, idx) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_results_run ON agent_results(run_id)",
    "CREATE INDEX IF NOT EXISTS idx_results_status ON agent_results(exit_status)",
    """
    CREATE TABLE IF NOT EXISTS pr_links (
        run_id     TEXT NOT NULL,
        group_name TEXT NOT NULL,
        url        TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (run_id, group_name),
        FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pr_run ON pr_links(run_id)",
]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(val: Any) -> str:
    if val is None:
        return "[]"
    return json.dumps(val, default=str)


def _json_loads(s: Optional[str]) -> Any:
    if not s:
        return []
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return []


def _canon_repo(repo: Any) -> str:
    """Canonicalize a repo path for stable equality across writes/reads.

    Resolves symlinks and normalizes separators. Non-existent components
    (e.g. test fixtures pointing at fake paths) pass through — Phase 4
    just needs the same input to produce the same output, not validation.
    """
    if repo is None:
        return ""
    return str(Path(repo).resolve())


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(db_path),
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=5000")
    return conn


def _init_schema(conn: sqlite3.Connection) -> None:
    cur = conn.cursor()
    cur.execute("BEGIN IMMEDIATE")
    try:
        for stmt in _SCHEMA_V1_STATEMENTS:
            cur.execute(stmt)
        cur.execute("SELECT version FROM schema_version")
        row = cur.fetchone()
        if row is None:
            cur.execute(
                "INSERT INTO schema_version(version) VALUES (?)",
                (_SCHEMA_VERSION,),
            )
        elif row["version"] < _SCHEMA_VERSION:
            _migrate(conn, from_version=row["version"])
        elif row["version"] > _SCHEMA_VERSION:
            cur.execute("ROLLBACK")
            raise RuntimeError(
                f"state.db schema v{row['version']} is newer than this code "
                f"(v{_SCHEMA_VERSION}). Upgrade Rizz to read it."
            )
        cur.execute("COMMIT")
    except Exception:
        try:
            cur.execute("ROLLBACK")
        except sqlite3.OperationalError:
            pass
        raise


def _migrate(conn: sqlite3.Connection, *, from_version: int) -> None:
    """Migration stub. v1 has nothing to do; v2+ will add steps here."""
    if from_version >= _SCHEMA_VERSION:
        return
    # Future:
    # if from_version < 2:
    #     _migrate_to_v2(conn)
    raise RuntimeError(
        f"no migration path from v{from_version} to v{_SCHEMA_VERSION}"
    )


# --- Public API ---------------------------------------------------------


class RunStore:
    """SQLite-backed persistence for Rizz runs.

    Lifetime: typically one per `Rizz.run()` call (auto-created when
    `repo=` is provided). Multiple stores can point at the same DB file
    safely thanks to WAL + busy_timeout.
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path).resolve()
        self._conn = _connect(self.db_path)
        _init_schema(self._conn)
        self._lock = threading.Lock()

    @classmethod
    def for_repo(cls, repo: Path) -> "RunStore":
        repo = Path(repo).resolve()
        db_path = repo / RIZZ_WORKTREE_DIR_NAME / _DEFAULT_DB_NAME
        return cls(db_path)

    def close(self) -> None:
        try:
            self._conn.close()
        except sqlite3.Error:
            pass

    # --- writes ---------------------------------------------------------

    def create_run(
        self,
        run_id: str,
        *,
        repo: str,
        question: str,
        purpose: Optional[str] = None,
        multi_workspace: bool = True,
        open_pr: bool = False,
        cleanup_policy: str = "auto",
        started_at: Optional[str] = None,
    ) -> None:
        started = started_at or _utc_now_iso()
        canonical_repo = _canon_repo(repo)
        with self._lock:
            self._exec(
                """
                INSERT INTO runs (
                    run_id, repo, question, purpose, status, started_at,
                    multi_workspace, open_pr, cleanup_policy
                ) VALUES (?, ?, ?, ?, 'running', ?, ?, ?, ?)
                """,
                (
                    run_id,
                    canonical_repo,
                    question,
                    purpose,
                    started,
                    int(bool(multi_workspace)),
                    int(bool(open_pr)),
                    cleanup_policy,
                ),
            )

    def add_workspace(
        self,
        run_id: str,
        workspace: "Workspace",
        group_name: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._exec(
                """
                INSERT OR REPLACE INTO workspaces (
                    workspace_id, run_id, group_name, branch, base_ref,
                    cwd, owns_worktree, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    workspace.id,
                    run_id,
                    group_name,
                    workspace.branch,
                    workspace.base_ref,
                    str(workspace.cwd),
                    int(bool(workspace._owns_worktree)),
                    _utc_now_iso(),
                ),
            )

    def add_task(self, run_id: str, task: "Task") -> None:
        args_json = _json_dumps(list(task.args) if task.args else [])
        deps_json = _json_dumps(list(task.dependencies))
        with self._lock:
            self._exec(
                """
                INSERT OR REPLACE INTO tasks (
                    run_id, idx, name, args, dependencies, workspace_group,
                    is_join
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    int(task.idx),
                    task.name,
                    args_json,
                    deps_json,
                    task.workspace_group,
                    int(bool(task.is_join)),
                ),
            )

    def set_observation(
        self, run_id: str, task_idx: int, observation: Any
    ) -> None:
        """Persist a task observation.

        AgentResult-typed observations populate every field. Plain string
        observations land in `summary` only with `exit_status='ok'`. None
        observations (e.g., `is_join` tasks) are skipped.
        """
        if observation is None:
            return
        # Detect AgentResult-shaped observations duck-typed (no import cycle).
        if hasattr(observation, "__dataclass_fields__") and hasattr(
            observation, "exit_status"
        ):
            summary = getattr(observation, "summary", "") or ""
            diff = getattr(observation, "diff", "") or ""
            files = getattr(observation, "files_changed", []) or []
            branch = getattr(observation, "branch", None)
            commits = getattr(observation, "commits", []) or []
            exit_status = getattr(observation, "exit_status", "ok")
            error = getattr(observation, "error", None)
            transcript = getattr(observation, "transcript", None)
        else:
            summary = str(observation)
            diff = ""
            files = []
            branch = None
            commits = []
            exit_status = "ok"
            error = None
            transcript = None

        with self._lock:
            self._exec(
                """
                INSERT OR REPLACE INTO agent_results (
                    run_id, task_idx, summary, diff, files_changed, branch,
                    commits, exit_status, error, transcript, finished_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    int(task_idx),
                    summary,
                    diff,
                    _json_dumps(list(files)),
                    branch,
                    _json_dumps(list(commits)),
                    exit_status,
                    error,
                    transcript,
                    _utc_now_iso(),
                ),
            )

    def set_run_status(
        self,
        run_id: str,
        status: str,
        *,
        finished_at: Optional[str] = None,
        raw_answer: Optional[str] = None,
        thinking_process: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        with self._lock:
            self._exec(
                """
                UPDATE runs SET
                    status = ?,
                    finished_at = COALESCE(?, finished_at),
                    raw_answer = COALESCE(?, raw_answer),
                    thinking_process = COALESCE(?, thinking_process),
                    error = COALESCE(?, error)
                WHERE run_id = ?
                """,
                (
                    status,
                    finished_at or _utc_now_iso(),
                    raw_answer,
                    thinking_process,
                    error,
                    run_id,
                ),
            )

    def add_pr(
        self,
        run_id: str,
        group_name: Optional[str],
        url: str,
    ) -> None:
        gname = group_name or _PR_SINGLE_SENTINEL
        with self._lock:
            self._exec(
                """
                INSERT OR REPLACE INTO pr_links (
                    run_id, group_name, url, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (run_id, gname, url, _utc_now_iso()),
            )

    def delete_run(self, run_id: str) -> None:
        with self._lock:
            self._exec("DELETE FROM runs WHERE run_id = ?", (run_id,))

    # --- reads ----------------------------------------------------------

    def get_run(self, run_id: str) -> Optional[RunRecord]:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        if row is None:
            return None
        workspaces = self.get_workspaces(run_id)
        tasks = self.get_tasks(run_id)
        results = self.get_agent_results(run_id)
        prs = self.get_pr_links(run_id)
        summary = RunSummary(
            run_id=row["run_id"],
            repo=row["repo"],
            question=row["question"],
            status=row["status"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            num_workspaces=len(workspaces),
            num_prs=len(prs),
        )
        return RunRecord(
            summary=summary,
            purpose=row["purpose"],
            multi_workspace=bool(row["multi_workspace"]),
            open_pr=bool(row["open_pr"]),
            cleanup_policy=row["cleanup_policy"],
            raw_answer=row["raw_answer"],
            thinking_process=row["thinking_process"],
            error=row["error"],
            workspaces=workspaces,
            tasks=tasks,
            results=results,
            prs=prs,
        )

    def list_runs(
        self,
        *,
        repo: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> List[RunSummary]:
        clauses: List[str] = []
        params: List[Any] = []
        if repo is not None:
            clauses.append("repo = ?")
            params.append(_canon_repo(repo))
        if status is not None and status != "all":
            clauses.append("status = ?")
            params.append(status)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"""
            SELECT r.*,
                (SELECT COUNT(*) FROM workspaces w WHERE w.run_id = r.run_id) AS nws,
                (SELECT COUNT(*) FROM pr_links p   WHERE p.run_id = r.run_id) AS npr
            FROM runs r
            {where}
            ORDER BY started_at DESC
            LIMIT ?
        """
        params.append(int(limit))
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
        out: List[RunSummary] = []
        for row in rows:
            out.append(
                RunSummary(
                    run_id=row["run_id"],
                    repo=row["repo"],
                    question=row["question"],
                    status=row["status"],
                    started_at=row["started_at"],
                    finished_at=row["finished_at"],
                    num_workspaces=int(row["nws"] or 0),
                    num_prs=int(row["npr"] or 0),
                )
            )
        return out

    def get_workspaces(self, run_id: str) -> List[WorkspaceRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM workspaces WHERE run_id = ?
                ORDER BY created_at ASC, workspace_id ASC
                """,
                (run_id,),
            ).fetchall()
        return [
            WorkspaceRecord(
                workspace_id=r["workspace_id"],
                run_id=r["run_id"],
                group_name=r["group_name"],
                branch=r["branch"],
                base_ref=r["base_ref"],
                cwd=r["cwd"],
                owns_worktree=bool(r["owns_worktree"]),
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def get_tasks(self, run_id: str) -> List[TaskRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM tasks WHERE run_id = ? ORDER BY idx ASC",
                (run_id,),
            ).fetchall()
        return [
            TaskRecord(
                run_id=r["run_id"],
                idx=int(r["idx"]),
                name=r["name"],
                args=_json_loads(r["args"]),
                dependencies=[int(x) for x in _json_loads(r["dependencies"])],
                workspace_group=r["workspace_group"],
                is_join=bool(r["is_join"]),
            )
            for r in rows
        ]

    def get_agent_results(self, run_id: str) -> List[AgentResultRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM agent_results WHERE run_id = ? ORDER BY task_idx ASC",
                (run_id,),
            ).fetchall()
        return [
            AgentResultRecord(
                run_id=r["run_id"],
                task_idx=int(r["task_idx"]),
                summary=r["summary"] or "",
                diff=r["diff"] or "",
                files_changed=[str(x) for x in _json_loads(r["files_changed"])],
                branch=r["branch"],
                commits=[str(x) for x in _json_loads(r["commits"])],
                exit_status=r["exit_status"] or "ok",
                error=r["error"],
                transcript=r["transcript"],
                finished_at=r["finished_at"],
            )
            for r in rows
        ]

    def get_pr_links(self, run_id: str) -> List[PrLinkRecord]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM pr_links WHERE run_id = ?
                ORDER BY created_at ASC, group_name ASC
                """,
                (run_id,),
            ).fetchall()
        return [
            PrLinkRecord(
                run_id=r["run_id"],
                group_name=r["group_name"],
                url=r["url"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def all_run_ids_for_repo(self, repo: str) -> List[str]:
        canonical = _canon_repo(repo)
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT run_id FROM runs WHERE repo = ?
                ORDER BY started_at DESC
                """,
                (canonical,),
            ).fetchall()
        return [r["run_id"] for r in rows]

    # --- maintenance ----------------------------------------------------

    async def vacuum(self) -> None:
        """VACUUM is slow; run via to_thread so we don't stall the loop."""
        await asyncio.to_thread(self._vacuum_sync)

    def _vacuum_sync(self) -> None:
        with self._lock:
            self._conn.execute("VACUUM")

    # --- internal -------------------------------------------------------

    def _exec(self, sql: str, params: tuple = ()) -> None:
        cur = self._conn.cursor()
        cur.execute("BEGIN IMMEDIATE")
        try:
            cur.execute(sql, params)
            cur.execute("COMMIT")
        except Exception:
            try:
                cur.execute("ROLLBACK")
            except sqlite3.OperationalError:
                pass
            raise
