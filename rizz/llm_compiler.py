import asyncio
import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Union, cast

from langchain.callbacks.manager import (
    AsyncCallbackManagerForChainRun,
    CallbackManagerForChainRun,
)
from langchain.chat_models.base import BaseChatModel
from langchain.llms import BaseLLM
from langchain.llms.base import BaseLLM
from langchain.prompts.base import StringPromptValue

from .callbacks import AsyncStatsCallbackHandler
from .chain import Chain
from .constants import JOINNER_REPLAN, JOINNER_FINISH
from .planner import Planner
from .metaplanner import MetaPlanner
from .task_fetching_unit import Task, TaskFetchingUnit
from .base import StructuredTool, Tool
from .logger_utils import log, log_task_execution
from .prompts import NO_ANWER_REPLY, TABLE_OUTPUT_PROMPT, MULTI_WS_OUTPUT_PROMPT
from .constants import END_OF_PLAN
from .topology import Topology, parse_topology_block
from .conflicts import detect_conflicts, render_conflict_report
from .agents.merge_plan import MergePlan, parse_merge_plan
from . import git_utils, gh_utils


def _store_safe(label: str, fn, *args, **kwargs):
    """Run a `RunStore` write best-effort; never let DB hiccups crash the run."""
    try:
        fn(*args, **kwargs)
    except Exception as e:
        log(f"store write {label} failed: {e}")

class LLMCompilerAgent:
    """Self defined agent for LLM Compiler."""

    def __init__(self, llm: BaseLLM) -> None:
        self.llm = llm

    async def arun(self, prompt: str, callbacks=None) -> str:
        response = await self.llm.agenerate_prompt(
            prompts=[StringPromptValue(text=prompt)],
            stop=["<END_OF_RESPONSE>"],
            callbacks=callbacks,
        )
        if isinstance(self.llm, BaseChatModel):
            return response.generations[0][0].message.content

        if isinstance(self.llm, BaseLLM):
            return response.generations[0][0].text

        raise ValueError("LLM must be either BaseChatModel or BaseLLM")


class LLMCompiler(Chain, extra="allow"):
    """LLMCompiler Engine."""

    """The step container to use."""
    input_key: str = "input"
    output_key: str = "output"

    def __init__(
        self,
        llm: BaseLLM,
        max_replans: int,
        benchmark: bool,
        message_manager,
        **kwargs,
    ) -> None:
        """
        Args:
            max_replans: Maximum number of replans to do.
            benchmark: Whether to collect benchmark stats.

        """
        super().__init__(**kwargs)

        
        self.meta_planner = MetaPlanner()
        self.planner = Planner()
        self.llm = llm
        self.agent = LLMCompilerAgent(llm)
        
        self.planner_stream = False
        self.max_replans = max_replans
        self.message_manager = message_manager

        # callbacks
        self.benchmark = False
        if benchmark:
            self.planner_callback = AsyncStatsCallbackHandler(stream=False)
            self.executor_callback = AsyncStatsCallbackHandler(stream=False)
        else:
            self.planner_callback = None
            self.executor_callback = None
            
    def get(self, key):
        return getattr(self, key, None)

    def get_all_stats(self):
        stats = {}
        if self.benchmark:
            stats["planner"] = self.planner_callback.get_stats()
            stats["executor"] = self.executor_callback.get_stats()
            stats["total"] = {
                k: v + stats["executor"].get(k, 0) for k, v in stats["planner"].items()
            }

        return stats

    def reset_all_stats(self):
        if self.planner_callback:
            self.planner_callback.reset()
        if self.executor_callback:
            self.executor_callback.reset()

    @property
    def input_keys(self) -> List[str]:
        return [self.input_key]

    @property
    def output_keys(self) -> List[str]:
        return [self.output_key]

    # TODO(sk): move all join related functions to a separate class

    def _parse_joinner_output(self, raw_answer: str) -> tuple[str, str, bool]:
        """
        Parse the joinner output format which is expected to be:
        ```
        Thought: xxx
        Action: Finish/Replan(yyy)
        ```

        Returns:
            tuple containing:
                thought (str): The thought content
                answer (str): The answer content inside Finish() or Replan()
                is_replan (bool): Whether this is a replan action
        """
        # Extract thought
        thought_pattern = r"Thought:\s*([\s\S]*?)(?=\s*Action:|$)"
        thought_match = re.search(thought_pattern, raw_answer)
        thought = thought_match.group(1).strip() if thought_match else ""
        
        # Modified action pattern to handle missing closing parenthesis
        action_pattern = r"Action:\s*(Finish|Replan)\(([\s\S]*?)(?:\s*\)\s*$|$)"# Made ) optional
        
        action_match = re.search(action_pattern, raw_answer)
        answer = ""
        is_replan = False
        
        if action_match:
            action_type = action_match.group(1)
            answer = action_match.group(2).strip()
            is_replan = (action_type == "Replan")
        
        return thought, answer, is_replan

    def _generate_context_for_replanner(
        self, tasks: Mapping[int, Task], joinner_thought: str
    ) -> str:
        """Formatted like this:
        ```
        1. action 1
        Observation: xxx
        2. action 2
        Observation: yyy
        ...
        Thought: joinner_thought
        ```
        """
        previous_plan_and_observations = "\n".join(
            [
                task.get_though_action_observation(
                    include_action=True, include_action_idx=True
                )
                for task in tasks.values()
                if not task.is_join
            ]
        )
        joinner_thought = f"Thought: {joinner_thought}"
        context = "\n\n".join([previous_plan_and_observations, joinner_thought])
        return context

    def _format_contexts(self, contexts: Sequence[str]) -> str:
        """contexts is a list of context
        each context is formatted as the description of _generate_context_for_replanner
        """
        formatted_contexts = ""
        for context in contexts:
            formatted_contexts += f"Previous Plan:\n\n{context}\n\n"
        formatted_contexts += "Current Plan:\n\n"
        return formatted_contexts

    async def join(
        self, inputs: Dict[str, Any], agent_scratchpad: str, is_final: bool , 
        joinner_prompt_final_in , joinner_prompt_in
    ) -> str:

        input_query = inputs["input"]
        is_table_format = inputs["is_table_format"]

        if is_final:
            joinner_prompt = joinner_prompt_final_in
        else:
            joinner_prompt = joinner_prompt_in

        if is_table_format:
            joinner_prompt = TABLE_OUTPUT_PROMPT

        prompt = (
            f"{joinner_prompt}\n"  # Instructions and examples
            f"Question: {input_query}\n\n"  # User input query
            f"{agent_scratchpad}\n"
        )
        # log("Joining prompt:\n", prompt, block=True)
        response = await self.agent.arun(
            prompt, callbacks=[self.executor_callback] if self.benchmark else None
        )
        raw_answer = cast(str, response)
        log("Question: \n", input_query, block=True)
        log("Raw Answer: \n", raw_answer, block=True)
        thought, answer, is_replan = self._parse_joinner_output(raw_answer)
        if is_final:
            # If final, we don't need to replan
            if is_replan:
                answer = NO_ANWER_REPLY
            is_replan = False
        return thought, answer, is_replan

    def _call(
        self,
        inputs: Dict[str, Any],
        run_manager: Optional[CallbackManagerForChainRun] = None,
    ):
        raise NotImplementedError("LLMCompiler is async only.")

    async def _acall(
        self,
        inputs: Dict[str, Any]

    ) -> Dict[str, Any]:
        """
        inputs = dict
        keys:
        planner_example_prompt
        planner_example_prompt_replan
        joinner_prompt
        joinner_prompt_final
        input
        purpose
        tools
        """
        if not inputs['planner_example_prompt_replan']:
            log(
                "Replan example prompt not specified, using the same prompt as the planner."
            )
            planner_example_prompt_replan = inputs['planner_example_prompt']
        planner_example_prompt = inputs['planner_example_prompt']
        joinner_prompt = inputs['joinner_prompt']
        joinner_prompt_final = inputs['joinner_prompt_final'] or joinner_prompt
            
        contexts = []
        joinner_thought = ""
        agent_scratchpad = ""
        answer = ""  # Initialize answer to prevent UnboundLocalError
        is_final_iter = False  # Initialize is_final_iter to prevent UnboundLocalError


        # Get meta data with thinking process
        meta_data_result = await self.meta_planner.retrieve_meta_data(inputs['input'],inputs['purpose'],inputs['instructions'],inputs["tools"],self.llm,None,inputs.get("query_understanding", ""),inputs.get("temporal_context", ""),inputs.get("research_approach", ""),inputs.get("dos", ""),inputs.get("donts", ""),inputs.get("meta_example", ""))
        #question: str, purpose : str, instructions: str , tools: list[str], llm, message_manager
        thinking_process = meta_data_result.get("thinking_process", "")
        meta_data = meta_data_result.get("meta_plan", "")

        # --- Phase 3: parse topology and allocate per-group workspaces ----
        manager = inputs.get("workspace_manager")
        single_ws = inputs.get("workspace")
        multi_ws_enabled = inputs.get("multi_workspace", True)

        topology: Optional[Topology] = (
            parse_topology_block(meta_data) if multi_ws_enabled else None
        )
        group_workspaces: Dict[str, Any] = {}

        if topology and topology.is_multi and manager is not None:
            log("phase3: topology detected", topology, block=True)
            for name in topology.groups:
                ws = await manager.allocate(branch_hint=name)
                group_workspaces[name] = ws
        elif topology and topology.is_multi and manager is None:
            log(
                "phase3: topology detected but no workspace manager "
                "(no repo= passed to Rizz.run); falling back to "
                "single-workspace mode."
            )
            topology = None

        is_multi = bool(group_workspaces)
        # Pick joiner prompts: multi-ws runs always use MULTI_WS_OUTPUT_PROMPT.
        effective_joinner_prompt = (
            MULTI_WS_OUTPUT_PROMPT if is_multi else joinner_prompt
        )
        effective_joinner_prompt_final = (
            MULTI_WS_OUTPUT_PROMPT if is_multi else joinner_prompt_final
        )

        # --- Phase 4: persist workspace allocations -------------------
        store = inputs.get("store")
        run_id = inputs.get("run_id")
        if store is not None and run_id is not None:
            if is_multi:
                for name, ws in group_workspaces.items():
                    _store_safe(
                        "add_workspace", store.add_workspace, run_id, ws, name
                    )
            elif single_ws is not None:
                _store_safe(
                    "add_workspace", store.add_workspace, run_id, single_ws, None
                )

        for i in range(self.max_replans):
            is_first_iter = i == 0
            is_final_iter = i == self.max_replans - 1

            if is_multi:
                task_fetching_unit = TaskFetchingUnit(
                    workspaces=group_workspaces,
                    default_group=topology.default_group if topology else None,
                )
            else:
                task_fetching_unit = TaskFetchingUnit(
                    workspace=inputs.get("workspace")
                )
            #llm,example_prompt,example_prompt_replan,tools,stop,inputs,meta_data,is_replan,callbacks
            tasks = await self.planner.plan(
                llm=self.llm,
                example_prompt=planner_example_prompt,
                example_prompt_replan=planner_example_prompt_replan,
                inputs=inputs,
                meta_data=meta_data,
                tools=inputs["tools"],
                stop=[END_OF_PLAN],
                is_replan=not is_first_iter,
                # callbacks=run_manager.get_child() if run_manager else None,
                callbacks=[self.planner_callback]
                if self.planner_callback
                else None,
            )
            log("Graph of tasks: ", tasks, block=True)
            if self.benchmark:
                self.planner_callback.additional_fields["num_tasks"] = len(tasks)

            # Phase 4: persist task graph (replan iterations OVERWRITE prior
            # rows for the same idx — last replanner wins).
            if store is not None and run_id is not None:
                for task in tasks.values():
                    _store_safe("add_task", store.add_task, run_id, task)

            task_fetching_unit.set_tasks(tasks)
            if self.message_manager is not None:
                await self.message_manager.send_message("System is processing data")
            await task_fetching_unit.schedule()
            tasks = task_fetching_unit.tasks

            # Phase 4: persist agent observations.
            if store is not None and run_id is not None:
                for idx, task in tasks.items():
                    if task.observation is not None and not task.is_join:
                        _store_safe(
                            "set_observation",
                            store.set_observation,
                            run_id,
                            idx,
                            task.observation,
                        )
            # collect thought-action-observation
            agent_scratchpad += "\n\n"
            agent_scratchpad += "".join(
                [
                    task.get_though_action_observation(
                        include_action=True, include_thought=True
                    )
                    for task in tasks.values()
                    if not task.is_join
                ]
            )
            agent_scratchpad = agent_scratchpad.strip()

            # Phase 3: append a conflict report between live workspace branches.
            if is_multi:
                branches = [
                    ws.branch
                    for ws in group_workspaces.values()
                    if ws.branch is not None
                ]
                try:
                    conflict_pairs = await detect_conflicts(
                        manager.repo, branches
                    )
                except Exception as e:
                    log("conflict detection failed; skipping report: %s" % e)
                    conflict_pairs = []
                conflict_report = render_conflict_report(conflict_pairs)
                if conflict_report:
                    agent_scratchpad += "\n\n" + conflict_report

            log("Agent scratchpad:\n", agent_scratchpad, block=True)
            if self.message_manager is not None:
                await self.message_manager.send_message("preparing answer")
            joinner_thought, answer, is_replan = await self.join(
                inputs,
                agent_scratchpad=agent_scratchpad,
                is_final=is_final_iter,
                joinner_prompt_final_in=effective_joinner_prompt_final,
                joinner_prompt_in=effective_joinner_prompt,
            )
            if not is_replan:
                log("Break out of replan loop.")
                if answer != NO_ANWER_REPLY:
                    log("Formatted Answer: \n", answer, block=True)
                    log_task_execution(tasks=tasks, final_answer=answer)
                break

            # Collect contexts for the subsequent replanner
            context = self._generate_context_for_replanner(
                tasks=tasks, joinner_thought=joinner_thought
            )
            contexts.append(context)
            formatted_contexts = self._format_contexts(contexts)
            log("Contexts:\n", formatted_contexts, block=True)
            inputs["context"] = formatted_contexts

        if is_final_iter:
            log("Reached max replan limit.")

        # --- Phase 3: parse merge plan; optionally open PRs ---
        merge_plan: Optional[MergePlan] = None
        pr_urls: List[str] = []
        if is_multi and not is_replan and answer:
            merge_plan = parse_merge_plan(
                answer, all_groups=list(group_workspaces.keys())
            )
            if inputs.get("open_pr"):
                pr_urls = await self._open_prs(
                    manager=manager,
                    group_workspaces=group_workspaces,
                    merge_plan=merge_plan,
                    pr_base_branch=inputs.get("pr_base_branch"),
                    pr_draft=inputs.get("pr_draft", False),
                    input_question=inputs.get("input", ""),
                )
                # Phase 4: persist PR links by group, in merge_order.
                if store is not None and run_id is not None and pr_urls:
                    order = (
                        merge_plan.merge_order
                        or merge_plan.keep
                        or list(group_workspaces.keys())
                    )
                    for group, url in zip(order, pr_urls):
                        _store_safe(
                            "add_pr", store.add_pr, run_id, group, url
                        )

        return {
            self.output_key: answer,
            "thinking_process": thinking_process,
            "merge_plan": merge_plan,
            "pr_urls": pr_urls,
            "topology": topology,
        }

    async def _open_prs(
        self,
        *,
        manager,
        group_workspaces: Dict[str, Any],
        merge_plan: MergePlan,
        pr_base_branch: Optional[str],
        pr_draft: bool,
        input_question: str,
    ) -> List[str]:
        """Push branches in `merge_plan.merge_order` (or `keep`) and open
        a PR per branch. Returns the list of PR URLs.

        Skips silently (and logs) if `gh` is unavailable, no `origin`
        remote is configured, or the default branch can't be detected.
        Per-branch failures don't abort the loop — each PR open is
        attempted independently.
        """
        if not await gh_utils.is_gh_available():
            log("auto-PR skipped: `gh` CLI not on PATH")
            return []
        base = pr_base_branch or await git_utils.get_default_branch(
            manager.repo
        )
        if base is None:
            log(
                "auto-PR skipped: cannot detect default branch; "
                "set pr_base_branch= explicitly"
            )
            return []
        if await git_utils.get_remote_url(manager.repo) is None:
            log("auto-PR skipped: no `origin` remote configured")
            return []

        order = merge_plan.merge_order or merge_plan.keep
        urls: List[str] = []
        short_question = (input_question or "").splitlines()[0][:60]
        for group in order:
            ws = group_workspaces.get(group)
            if ws is None or ws.branch is None:
                log(f"auto-PR: skipping group {group!r} (no branch)")
                continue
            try:
                await git_utils.push(manager.repo, ws.branch)
            except git_utils.GitError as e:
                log(f"auto-PR: push of {ws.branch} failed: {e}")
                continue
            title = f"[{group}] {short_question}".rstrip()
            body = (
                f"Generated by Rizz for question:\n\n"
                f"> {input_question}\n\n"
                f"Group: `{group}`\n"
                f"Branch: `{ws.branch}`\n\n"
                f"Joiner notes:\n{merge_plan.notes or '(none)'}\n"
            )
            try:
                url = await gh_utils.gh_pr_create(
                    manager.repo,
                    base=base,
                    head=ws.branch,
                    title=title,
                    body=body,
                    draft=pr_draft,
                )
                urls.append(url)
                # Mark the workspace so future cleanup can be PR-aware.
                ws._pr_opened = True
            except gh_utils.GhError as e:
                log(f"auto-PR: gh pr create failed for {group}: {e}")
        return urls