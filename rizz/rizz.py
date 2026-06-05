import time
import asyncio
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Optional, Union
from .llm_compiler import LLMCompiler
from .tools.tool_generator import ToolGenerator
from .workspace import Workspace
from .workspace_manager import WorkspaceManager
from .store import RunStore


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _allocate_run_id(existing_check=None) -> str:
    """Generate a `run-<8 hex>` ID. UNIQUE-constraint retry handled in
    the SQL layer; this just mints fresh randomness."""
    return "run-" + secrets.token_hex(4)


class Rizz:
    def __init__(  # Fixed: was _init_ (missing underscores)
        self,
        llm,
        message_manager=None,
        memory=[],
        max_replan=3,
        verbose=False,
    ):
        self.message_manager = message_manager
        self.llm = llm
        self.memory = memory
        self.max_replan = max_replan
        self.verbose = verbose
        self.is_tables = False
        self.agent = LLMCompiler(
            name="LLMCompiler",  # Required by Chain base class
            llm=self.llm,
            max_replans=self.max_replan,
            benchmark=False,
            message_manager=self.message_manager
        )
        # Phase 4: rich result + persistence handles, populated per run().
        # Tuple return is preserved for backward-compat; callers who want
        # the merge plan / pr_urls / topology can read these.
        self.last_run_id: Optional[str] = None
        self.last_result: Optional[dict] = None
        self.last_store: Optional[RunStore] = None

    async def arun_and_time(self, func, *args, **kwargs):
        """Helper function to run and time a function.
        Raises exceptions to caller for proper error handling.
        """
        start = time.time()
        try:
            result = await func(*args, **kwargs)
        except Exception as e:
            print(f"Error: {e}")
            # Re-raise the exception instead of returning "ERROR"
            # This allows proper error handling in the calling code
            raise
        end = time.time()
        return result, end - start

    async def run(
        self,
        question,
        purpose,
        tools,
        instructions,
        query_understanding="",
        temporal_context="",
        research_approach="",
        dos="",
        donts="",
        meta_example="",
        planner_example_prompt="",
        joinner_prompt="",
        tool_path=None,
        repo: Optional[Union[str, Path]] = None,
        workspace: Optional[Workspace] = None,
        workspace_env: Optional[dict] = None,
        worktree_root: Optional[Union[str, Path]] = None,
        # --- Phase 3 ---
        multi_workspace: bool = True,
        open_pr: bool = False,
        pr_base_branch: Optional[str] = None,
        pr_draft: bool = False,
        # --- Phase 4 ---
        cleanup: str = "auto",
        run_id: Optional[str] = None,
        store: Optional[RunStore] = None,
    ):
        """
        Runs the compiler asynchronously

        Args:
        planner_example_prompt
        planner_example_prompt_replan
        joinner_prompt
        joinner_prompt_final
        input
        purpose
        instructions
        tools = [{class:search_tool ,name:fb_search, domains :["website1",],extra_info:"searches over facebook"}]]
        repo: optional path to a git repo; if provided, allocates an isolated worktree per run.
        workspace: optional pre-built Workspace (advanced; caller manages lifecycle).
        workspace_env: extra env vars passed to the Workspace.
        worktree_root: override <repo>/.rizz/worktrees/.
        multi_workspace: if True (default), MetaPlanner may emit a
            WORKSPACE_TOPOLOGY block to fan out into multiple branches.
            Set False to force single-workspace mode.
        open_pr: if True, push each kept branch and open a `gh pr create`
            against `pr_base_branch` (default: detected `origin` HEAD).
        pr_base_branch: base branch for auto-PR (default: detected).
        pr_draft: open draft PRs.
        cleanup: workspace cleanup policy. "auto" (default) always cleans
            up — matches Phase 1-3 behavior. "never" leaves worktrees and
            branches in place so the run can be inspected via the CLI.
            "on_success" cleans up only when the run completes without an
            exception. The CLI defaults to "never".
        run_id: optional pre-assigned run identifier (e.g. for resuming
            from a known ID). Auto-generated as `run-<8hex>` if omitted.
        store: optional pre-built RunStore. If omitted and `repo=` is set,
            a store is auto-opened at <repo>/.rizz/state.db.

        Returns:
            A 3-tuple `(raw_answer, [], thinking_process)` for backward
            compat. Rich data (run_id, merge_plan, pr_urls, topology)
            lives on `engine.last_result` and in the `RunStore` at
            `engine.last_store`.
        """
        # Create the input dictionary

        input_dict = {"input": question}
        input_dict["is_table_format"] = self.is_tables
        input_dict["planner_example_prompt"]=planner_example_prompt
        input_dict["planner_example_prompt_replan"]=None
        input_dict["joinner_prompt"]=joinner_prompt
        input_dict["joinner_prompt_final"]=None
        input_dict["purpose"]=purpose
        input_dict["instructions"]=instructions
        input_dict["query_understanding"]=query_understanding
        input_dict["temporal_context"]=temporal_context
        input_dict["research_approach"]=research_approach
        input_dict["dos"]=dos
        input_dict["donts"]=donts
        input_dict["meta_example"]=meta_example


        #tools_list= [{class:search_tool ,name:fb_search, domains :["website1",],extra_info:"searches over facebook"}]]
        input_dict["tools"]=ToolGenerator(tools,tool_path)
        print("using tools",input_dict["tools"])

        if self.is_tables:
            table_context = f"Answer this question: {question}"
            input_dict["input"] = f"{table_context}"

        # --- Phase 4: assign run_id, open store ----------------------
        if run_id is None:
            run_id = _allocate_run_id()
        self.last_run_id = run_id
        owns_store = False
        if store is None and repo is not None:
            store = RunStore.for_repo(Path(repo))
            owns_store = True
        self.last_store = store

        if store is not None:
            try:
                store.create_run(
                    run_id,
                    repo=str(Path(repo).resolve()) if repo else "",
                    question=question,
                    purpose=purpose,
                    multi_workspace=multi_workspace,
                    open_pr=open_pr,
                    cleanup_policy=cleanup,
                    started_at=_utc_now_iso(),
                )
            except Exception as e:
                # Persistence shouldn't break the run.
                print(f"warning: store.create_run failed: {e}")

        manager = None
        ws = workspace
        owns_workspace = False
        run_status = "completed"
        run_error: Optional[str] = None
        result: Any = None
        try:
            if ws is None and repo is not None:
                manager = WorkspaceManager(
                    repo=Path(repo),
                    root=Path(worktree_root) if worktree_root else None,
                )
                ws = await manager.allocate(env_vars=workspace_env)
                owns_workspace = True
            input_dict["workspace"] = ws
            # Phase 3 plumbing: the compiler may allocate per-group
            # workspaces from this manager.
            input_dict["workspace_manager"] = manager
            input_dict["multi_workspace"] = multi_workspace
            input_dict["open_pr"] = open_pr
            input_dict["pr_base_branch"] = pr_base_branch
            input_dict["pr_draft"] = pr_draft
            # Phase 4 plumbing: _acall persists tasks/observations/PRs.
            input_dict["store"] = store
            input_dict["run_id"] = run_id

            # Call the agent with our modified input
            result, _ = await self.arun_and_time(
                self.agent.acall,
                input_dict,
                callbacks=None,
            )
        except Exception as e:
            run_status = "failed"
            run_error = repr(e)
            raise
        finally:
            if owns_workspace and manager is not None:
                policy = {
                    "auto": "force",
                    "never": "skip",
                    "on_success": "on_success",
                }.get(cleanup, "force")
                await manager.cleanup_all(
                    policy=policy, run_status=run_status
                )
            if store is not None:
                # Best-effort: never let a stuck DB hide the actual run result.
                try:
                    raw_for_store = ""
                    thinking_for_store: Optional[str] = None
                    if isinstance(result, dict):
                        raw_for_store = result.get(self.agent.output_key, "") or ""
                        thinking_for_store = result.get("thinking_process")
                    store.set_run_status(
                        run_id,
                        run_status,
                        finished_at=_utc_now_iso(),
                        raw_answer=raw_for_store or None,
                        thinking_process=thinking_for_store,
                        error=run_error,
                    )
                except Exception as e:
                    print(f"warning: store.set_run_status failed: {e}")
                if owns_store:
                    try:
                        store.close()
                    except Exception:
                        pass
        print("initial : ",result)
        if isinstance(result, dict):
            raw_answer = result.get(self.agent.output_key, "")
        else:
            raw_answer = str(result) if result is not None else ""
        if isinstance(result, str):
            thinking_process = result
        elif isinstance(result, dict):
            thinking_process = result.get("thinking_process", "")
        else:
            thinking_process = ""

        # Stash the rich payload for callers who want more than the tuple.
        if isinstance(result, dict):
            self.last_result = {
                "run_id": run_id,
                "raw_answer": raw_answer,
                "thinking_process": thinking_process,
                "merge_plan": result.get("merge_plan"),
                "pr_urls": result.get("pr_urls", []),
                "topology": result.get("topology"),
            }
        else:
            self.last_result = {
                "run_id": run_id,
                "raw_answer": raw_answer,
                "thinking_process": thinking_process,
                "merge_plan": None,
                "pr_urls": [],
                "topology": None,
            }

        return raw_answer, [], thinking_process  # tuple shape preserved
    
