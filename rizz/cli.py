"""Phase 4 — `rizz` CLI.

Click-based command tree:

    rizz run    --repo PATH --question "..." [...]
    rizz list   [--repo PATH] [--status STATUS] [--limit N]
    rizz status RUN_ID [--show-diff] [--show-transcript]
    rizz resume RUN_ID
    rizz prs    RUN_ID
    rizz clean  [--repo PATH] [--keep-recent N] [--dry-run]

The CLI is shell-only; running from inside a notebook should shell out
rather than `cli(standalone_mode=False)` — async lifetimes get tangled
otherwise. We don't pull `nest_asyncio` for the CLI specifically.

Exit codes:
    0  success
    1  engine error (run failed at runtime)
    2  user error (bad args, missing run-id, repo not found)
    3  partial success (run finished but some PRs failed to open)
"""

from __future__ import annotations

import asyncio
import dataclasses
import importlib.util
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any, List, Optional

import click

from .agents.merge_plan import parse_merge_plan
from .constants import RIZZ_WORKTREE_DIR_NAME
from .run_record import RunRecord, RunSummary
from .store import RunStore


# --- helpers ----------------------------------------------------------


def _find_repo_root(start: Path) -> Optional[Path]:
    """Sync walk-up looking for a `.git` dir/file. Mirrors
    `git_utils.find_repo_root` but without async — Click commands
    can't await before parsing args.
    """
    cur = start.resolve()
    while True:
        if (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent


def _resolve_repo(repo: Optional[str]) -> Path:
    """Resolve --repo arg, defaulting to cwd's git root."""
    if repo:
        p = Path(repo).resolve()
        if not p.exists():
            raise click.UsageError(f"--repo path does not exist: {p}")
        return p
    found = _find_repo_root(Path.cwd())
    if found is None:
        raise click.UsageError(
            "no git repo detected in cwd; pass --repo PATH explicitly"
        )
    return found


def _open_store(repo: Path) -> RunStore:
    db = repo / RIZZ_WORKTREE_DIR_NAME / "state.db"
    if not db.exists():
        raise click.UsageError(
            f"no state.db at {db} — has any run executed for this repo?"
        )
    return RunStore(db)


def _truncate(s: str, n: int) -> str:
    if not s:
        return ""
    s = s.replace("\n", " ").strip()
    return s if len(s) <= n else (s[: n - 1] + "…")


def _load_question(question: Optional[str], file: Optional[Path]) -> str:
    """--question / --question-file / --question - mutex."""
    sources = [v for v in (question, file) if v is not None]
    if len(sources) != 1:
        raise click.UsageError(
            "exactly one of --question / --question-file is required"
        )
    if file is not None:
        return Path(file).read_text(encoding="utf-8").strip()
    if question == "-":
        return sys.stdin.read().strip()
    return question or ""


def _load_tools_module(path: Path):
    """Import a Python file by path; require get_llm() and get_tools()."""
    if not path.exists():
        raise click.UsageError(f"--tools-config not found: {path}")
    spec = importlib.util.spec_from_file_location("_rizz_user_tools", path)
    if spec is None or spec.loader is None:
        raise click.UsageError(f"cannot load --tools-config: {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if not hasattr(mod, "get_llm"):
        raise click.UsageError(
            f"--tools-config {path} must define `get_llm() -> llm`"
        )
    if not hasattr(mod, "get_tools"):
        raise click.UsageError(
            f"--tools-config {path} must define `get_tools() -> list[dict]`"
        )
    return mod


def _record_to_dict(rec: RunRecord) -> dict:
    """Convert a RunRecord (and nested records) to a JSON-friendly dict."""
    d = dataclasses.asdict(rec)
    # Normalize nested datetimes-as-strings already; nothing to recurse.
    return d


# --- top-level group --------------------------------------------------


@click.group()
@click.version_option(message="rizz CLI (Phase 4)")
def cli() -> None:
    """rizz — operate the LLMCompiler from the shell."""


# --- run --------------------------------------------------------------


@cli.command(name="run")
@click.option("--repo", required=True, help="Git repo to run against.")
@click.option("--question", default=None, help="Inline question. Use `-` to read stdin.")
@click.option(
    "--question-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Read question from a file.",
)
@click.option("--purpose", default="", help="Purpose / system prompt.")
@click.option("--instructions", default="", help="Engine instructions.")
@click.option(
    "--tools-config",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    required=True,
    help="Python file defining get_llm() and get_tools().",
)
@click.option("--open-pr/--no-open-pr", default=False)
@click.option("--multi-workspace/--no-multi-workspace", default=True)
@click.option(
    "--cleanup",
    type=click.Choice(["auto", "never", "on_success"]),
    default="never",
    help="Workspace cleanup policy. CLI default is `never` so status/resume work.",
)
@click.option("--run-id", default=None, help="Pre-assigned run id (auto if omitted).")
@click.option(
    "--joinner-prompt",
    default="",
    help="Override joiner prompt (advanced).",
)
@click.option(
    "--planner-example-prompt",
    default="",
    help="Override planner example prompt (advanced).",
)
@click.option("--json", "as_json", is_flag=True, default=False, help="Emit JSON.")
def cmd_run(
    repo: str,
    question: Optional[str],
    question_file: Optional[Path],
    purpose: str,
    instructions: str,
    tools_config: Path,
    open_pr: bool,
    multi_workspace: bool,
    cleanup: str,
    run_id: Optional[str],
    joinner_prompt: str,
    planner_example_prompt: str,
    as_json: bool,
) -> None:
    """Run the engine against REPO with QUESTION."""
    repo_path = _resolve_repo(repo)
    q = _load_question(question, question_file)
    tools_mod = _load_tools_module(tools_config)

    from .rizz import Rizz  # local import to avoid heavy startup

    engine = Rizz(llm=tools_mod.get_llm())

    # ToolGenerator scans a *directory* for *Tool classes; the user's
    # config file lives in that directory, so we pass the parent dir.
    tool_path = str(tools_config.parent)

    async def _runner() -> tuple:
        return await engine.run(
            question=q,
            purpose=purpose,
            tools=tools_mod.get_tools(),
            instructions=instructions,
            planner_example_prompt=planner_example_prompt,
            joinner_prompt=joinner_prompt,
            tool_path=tool_path,
            repo=str(repo_path),
            multi_workspace=multi_workspace,
            open_pr=open_pr,
            cleanup=cleanup,
            run_id=run_id,
        )

    try:
        raw_answer, _, thinking = asyncio.run(_runner())
    except Exception as e:
        click.echo(f"engine error: {e}", err=True)
        sys.exit(1)

    last = engine.last_result or {}
    pr_urls: List[str] = list(last.get("pr_urls") or [])

    if as_json:
        out = {
            "run_id": engine.last_run_id,
            "raw_answer": raw_answer,
            "thinking_process": thinking,
            "pr_urls": pr_urls,
            "merge_plan": (
                dataclasses.asdict(last["merge_plan"])
                if last.get("merge_plan") is not None
                else None
            ),
            "topology": (
                dataclasses.asdict(last["topology"])
                if last.get("topology") is not None
                else None
            ),
        }
        click.echo(json.dumps(out, indent=2, default=str))
    else:
        click.echo(f"run_id: {engine.last_run_id}")
        click.echo(f"status: completed")
        click.echo("answer:")
        click.echo(raw_answer)
        if pr_urls:
            click.echo("\nPRs:")
            for u in pr_urls:
                click.echo(f"  {u}")

    # Heuristic exit code: engine completed but no PRs when open_pr was on.
    if open_pr and not pr_urls and (last.get("merge_plan") is not None):
        sys.exit(3)


# --- list -------------------------------------------------------------


@cli.command(name="list")
@click.option("--repo", default=None)
@click.option(
    "--status",
    type=click.Choice(["running", "completed", "failed", "all"]),
    default="all",
)
@click.option("--limit", type=int, default=20)
@click.option("--json", "as_json", is_flag=True, default=False)
def cmd_list(repo: Optional[str], status: str, limit: int, as_json: bool) -> None:
    """List runs for a repo."""
    repo_path = _resolve_repo(repo)
    try:
        store = _open_store(repo_path)
    except click.UsageError as e:
        if as_json:
            click.echo("[]")
            return
        click.echo(str(e), err=True)
        sys.exit(0)  # empty list is a fine outcome
    try:
        runs = store.list_runs(repo=str(repo_path), status=status, limit=limit)
    finally:
        store.close()

    if as_json:
        click.echo(
            json.dumps([dataclasses.asdict(r) for r in runs], indent=2, default=str)
        )
        return

    if not runs:
        click.echo("(no runs)")
        return

    click.echo(
        f"{'RUN ID':<14} {'STATUS':<10} {'STARTED':<25} {'WS':>3} {'PRs':>4}  QUESTION"
    )
    for r in runs:
        click.echo(
            f"{r.run_id:<14} {r.status:<10} {r.started_at:<25} "
            f"{r.num_workspaces:>3} {r.num_prs:>4}  {_truncate(r.question, 35)}"
        )


# --- status -----------------------------------------------------------


@cli.command(name="status")
@click.argument("run_id")
@click.option("--repo", default=None)
@click.option("--json", "as_json", is_flag=True, default=False)
@click.option("--show-diff", is_flag=True, default=False)
@click.option("--show-transcript", is_flag=True, default=False)
def cmd_status(
    run_id: str,
    repo: Optional[str],
    as_json: bool,
    show_diff: bool,
    show_transcript: bool,
) -> None:
    """Show full detail for RUN_ID."""
    repo_path = _resolve_repo(repo)
    try:
        store = _open_store(repo_path)
    except click.UsageError as e:
        click.echo(str(e), err=True)
        sys.exit(2)

    try:
        rec = store.get_run(run_id)
    finally:
        store.close()

    if rec is None:
        click.echo(f"run not found: {run_id}", err=True)
        sys.exit(2)

    if as_json:
        click.echo(json.dumps(_record_to_dict(rec), indent=2, default=str))
        return

    s = rec.summary
    click.echo(f"Run {s.run_id}")
    click.echo(f"  Repo:        {s.repo}")
    click.echo(f"  Status:      {s.status}")
    click.echo(f"  Started:     {s.started_at}")
    click.echo(f"  Finished:    {s.finished_at or '(running)'}")
    click.echo(f"  Question:    {_truncate(s.question, 200)}")
    click.echo(f"  Cleanup:     {rec.cleanup_policy}")
    if rec.error:
        click.echo(f"  Error:       {rec.error}")

    click.echo(f"\nWorkspaces ({len(rec.workspaces)}):")
    for w in rec.workspaces:
        gn = f"[{w.group_name}]" if w.group_name else "[single]"
        click.echo(
            f"  {w.workspace_id} {gn}  branch={w.branch or '?'}  cwd={w.cwd}"
        )

    click.echo(f"\nTasks ({len(rec.tasks)}):")
    results_by_idx = {r.task_idx: r for r in rec.results}
    for t in rec.tasks:
        gn = f"[{t.workspace_group}]" if t.workspace_group else ""
        deps = ",".join(str(d) for d in t.dependencies) if t.dependencies else ""
        suffix = " (joiner)" if t.is_join else ""
        click.echo(f"  {t.idx:>2}. {t.name} {gn} deps=[{deps}]{suffix}")
        r = results_by_idx.get(t.idx)
        if r is not None:
            commit_brief = ",".join(c[:8] for c in r.commits) or "-"
            click.echo(
                f"      -> {r.exit_status}  commits={commit_brief}  "
                f"files={len(r.files_changed)}"
            )
            if r.summary:
                click.echo(f"      summary: {_truncate(r.summary, 200)}")
            if show_diff and r.diff:
                click.echo("      --- diff ---")
                for line in r.diff.splitlines()[:50]:
                    click.echo(f"        {line}")
                extra = max(0, len(r.diff.splitlines()) - 50)
                if extra:
                    click.echo(f"        ... [{extra} more lines]")
            if show_transcript and r.transcript:
                click.echo("      --- transcript ---")
                for line in r.transcript.splitlines()[:50]:
                    click.echo(f"        {line}")

    # Re-parse merge plan from raw_answer (Phase 3 stores the prose; Phase 4
    # doesn't add a separate column).
    if rec.raw_answer:
        groups = sorted({w.group_name for w in rec.workspaces if w.group_name})
        mp = parse_merge_plan(rec.raw_answer, all_groups=groups)
        if mp.parsed:
            click.echo("\nMerge plan:")
            click.echo(f"  keep:        {mp.keep}")
            click.echo(f"  archive:     {mp.archive}")
            click.echo(f"  merge_order: {mp.merge_order}")
            if mp.notes:
                click.echo(f"  notes:       {_truncate(mp.notes, 200)}")

    click.echo(f"\nPRs ({len(rec.prs)}):")
    for p in rec.prs:
        click.echo(f"  {p.group_name} -> {p.url}")

    if rec.raw_answer:
        click.echo("\nAnswer:")
        click.echo(rec.raw_answer)


# --- resume -----------------------------------------------------------


@cli.command(name="resume", help="Inspect a prior run (no mid-run resumption in v1).")
@click.argument("run_id")
@click.option("--repo", default=None)
def cmd_resume(run_id: str, repo: Optional[str]) -> None:
    """Print the worktree paths and final answer of RUN_ID.

    Phase 4 does NOT re-run unfinished tasks against existing workspaces.
    Mid-run resumption requires task-level checkpointing of the replan
    loop's state and a re-entry point inside the planner; both are
    Phase 5. Use this command to inspect a kept run (cleanup="never")
    or to recover the joiner output of a completed run.
    """
    repo_path = _resolve_repo(repo)
    try:
        store = _open_store(repo_path)
    except click.UsageError as e:
        click.echo(str(e), err=True)
        sys.exit(2)
    try:
        rec = store.get_run(run_id)
    finally:
        store.close()
    if rec is None:
        click.echo(f"run not found: {run_id}", err=True)
        sys.exit(2)

    click.echo(f"Run {run_id} status={rec.summary.status}")
    if rec.summary.status == "running":
        click.echo(
            "WARNING: status is 'running'. The originating process may still be alive.",
            err=True,
        )
    click.echo("\nWorktrees:")
    for w in rec.workspaces:
        exists = Path(w.cwd).exists()
        marker = "" if exists else "  (gone — cleaned up?)"
        click.echo(f"  {w.workspace_id} {w.cwd}{marker}")
    if rec.prs:
        click.echo("\nPRs:")
        for p in rec.prs:
            click.echo(f"  {p.group_name} -> {p.url}")
    if rec.raw_answer:
        click.echo("\nAnswer:")
        click.echo(rec.raw_answer)


# --- prs --------------------------------------------------------------


@cli.command(name="prs", help="List PR URLs for RUN_ID, one per line.")
@click.argument("run_id")
@click.option("--repo", default=None)
def cmd_prs(run_id: str, repo: Optional[str]) -> None:
    repo_path = _resolve_repo(repo)
    try:
        store = _open_store(repo_path)
    except click.UsageError as e:
        click.echo(str(e), err=True)
        sys.exit(2)
    try:
        rec = store.get_run(run_id)
    finally:
        store.close()
    if rec is None:
        click.echo(f"run not found: {run_id}", err=True)
        sys.exit(2)
    for p in rec.prs:
        click.echo(p.url)


# --- clean ------------------------------------------------------------


@cli.command(name="clean")
@click.option("--repo", default=None)
@click.option(
    "--keep-recent",
    type=int,
    default=10,
    help="Number of most-recent runs to keep (0 = delete all).",
)
@click.option("--dry-run", is_flag=True, default=False)
@click.option("--vacuum", is_flag=True, default=False)
def cmd_clean(
    repo: Optional[str], keep_recent: int, dry_run: bool, vacuum: bool
) -> None:
    """Delete old runs (and their workspaces/branches) from the store."""
    repo_path = _resolve_repo(repo)
    try:
        store = _open_store(repo_path)
    except click.UsageError as e:
        click.echo(str(e), err=True)
        sys.exit(0)
    try:
        all_ids = store.all_run_ids_for_repo(str(repo_path))
        if keep_recent > 0:
            to_delete = all_ids[keep_recent:]
        else:
            to_delete = list(all_ids)
        click.echo(
            f"keeping {min(keep_recent, len(all_ids))} of {len(all_ids)}; "
            f"would delete {len(to_delete)}"
        )
        for rid in to_delete:
            click.echo(f"  - {rid}")
        if dry_run:
            click.echo("(--dry-run: no changes made)")
            return
        for rid in to_delete:
            asyncio.run(_clean_run(store, repo_path, rid))
            store.delete_run(rid)
        if vacuum:
            asyncio.run(store.vacuum())
            click.echo("vacuum: done")
    finally:
        store.close()


async def _clean_run(store: RunStore, repo_path: Path, run_id: str) -> None:
    """Best-effort worktree + branch removal for a run being purged."""
    from . import git_utils  # local import; this command is rarely used

    rec = store.get_run(run_id)
    if rec is None:
        return
    for w in rec.workspaces:
        # Remove worktree if it still exists
        cwd = Path(w.cwd)
        if cwd.exists():
            try:
                await git_utils.worktree_remove(repo_path, cwd, force=True)
            except git_utils.GitError as e:
                click.echo(
                    f"  warn: worktree_remove({w.workspace_id}): {e}", err=True
                )
        # Try to delete the branch — only succeeds if no commits ahead.
        if w.branch:
            try:
                await git_utils.branch_delete(repo_path, w.branch, force=False)
            except git_utils.GitError:
                pass  # branch had commits or doesn't exist; leave it


# --- module entry point -----------------------------------------------


main = cli


if __name__ == "__main__":
    main()
