from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Collection, Dict, List, Optional

from .base import _accepts_workspace
from .logger_utils import log

if TYPE_CHECKING:
    from .workspace import Workspace

SCHEDULING_INTERVAL = 0.01  # seconds

# `$1` / `${1}` with optional `.field` suffix for AgentResult attribute access.
_ATTR_DEP_PATTERN = re.compile(r"\$\{?(\d+)\}?(?:\.([A-Za-z_][A-Za-z0-9_]*))?")


def _default_stringify_rule_for_arguments(args):
    def stringify(arg):
        # Handle strings by adding quotes around them
        if isinstance(arg, str):
            return f'"{arg}"'
        return str(arg)  # Default conversion for other types

    # If there's only one argument, wrap it in parentheses
    if len(args) == 1:
        return f"({stringify(args[0])})"
    else:
        # Handle multiple arguments as a tuple
        return "(" + ", ".join(stringify(arg) for arg in args) + ")"



def _replace_arg_mask_with_real_value(
    args, dependencies: List[int], tasks: Dict[str, Task]
):
    """Substitute `$N` / `${N}` / `$N.field` placeholders with prior observations.

    `$N`        → `str(tasks[N].observation)` (Phase 1 behavior, preserved)
    `$N.field`  → `str(getattr(observation, "field"))` if observation is a
                  dataclass-style object exposing that attribute. For plain
                  string observations, the placeholder is left literal so
                  the misuse surfaces in the joiner output instead of being
                  silently dropped.
    """
    if isinstance(args, (list, tuple)):
        return type(args)(
            _replace_arg_mask_with_real_value(item, dependencies, tasks)
            for item in args
        )
    if not isinstance(args, str):
        return args

    deps_set = {int(d) for d in dependencies}

    def _sub(m: re.Match) -> str:
        dep = int(m.group(1))
        field_name = m.group(2)
        if dep not in deps_set:
            return m.group(0)
        if dep not in tasks or tasks[dep].observation is None:
            return m.group(0)
        obs = tasks[dep].observation
        if field_name is None:
            return str(obs)
        val = getattr(obs, field_name, None)
        if val is None:
            # Dataclass-style observation? Drop the literal silently because
            # the agent presumably had the attribute and it was None/empty.
            # Plain-string observation? Preserve the literal so the misuse
            # is visible in the rendered scratchpad rather than swallowed.
            if hasattr(obs, "__dataclass_fields__"):
                return ""
            return f"{obs}.{field_name}"
        return str(val)

    return _ATTR_DEP_PATTERN.sub(_sub, args)


@dataclass
class Task:
    idx: int
    name: str
    tool: Callable
    args: Collection[Any]
    dependencies: Collection[int]
    stringify_rule: Optional[Callable] = None
    thought: Optional[str] = None
    observation: Optional[str] = None
    is_join: bool = False
    workspace: Optional["Workspace"] = None  # injected by TaskFetchingUnit, not the planner
    workspace_group: Optional[str] = None  # set by parser; selects the per-group workspace

    async def __call__(self) -> Any:
        log("running task")
        if self.workspace is not None and _accepts_workspace(self.tool):
            x = await self.tool(*self.args, workspace=self.workspace)
        else:
            x = await self.tool(*self.args)
        log("done task")
        return x

    def get_though_action_observation(
        self, include_action=True, include_thought=True, include_action_idx=False
    ) -> str:
        thought_action_observation = ""
        if self.thought and include_thought:
            thought_action_observation = f"Thought: {self.thought}\n"
        if include_action:
            idx = f"{self.idx}. " if include_action_idx else ""
            if self.stringify_rule:
                # If the user has specified a custom stringify rule for the
                # function argument, use it
                thought_action_observation += f"{idx}{self.stringify_rule(self.args)}\n"
            else:
                # Otherwise, we have a default stringify rule
                thought_action_observation += (
                    f"{idx}{self.name}"
                    f"{_default_stringify_rule_for_arguments(self.args)}\n"
                )
        if self.observation is not None:
            thought_action_observation += f"Observation: {self.observation}\n"
        return thought_action_observation


class TaskFetchingUnit:
    tasks: Dict[str, Task]
    tasks_done: Dict[str, asyncio.Event]
    remaining_tasks: set[str]

    def __init__(
        self,
        workspace: Optional["Workspace"] = None,
        workspaces: Optional[Dict[str, "Workspace"]] = None,
        default_group: Optional[str] = None,
    ):
        """Single-workspace mode (Phase 1/2) and multi-workspace mode
        (Phase 3) are both supported. Pass either `workspace=` (single)
        or `workspaces=` + `default_group=` (multi). If both are given,
        `workspaces` wins and `workspace` is ignored.
        """
        self.tasks = {}
        self.tasks_done = {}
        self.remaining_tasks = set()
        self.workspace = workspace
        self.workspaces = dict(workspaces) if workspaces else {}
        self.default_group = default_group

    def _resolve_workspace(self, task: Task) -> Optional["Workspace"]:
        if self.workspaces:
            group = task.workspace_group or self.default_group
            ws = self.workspaces.get(group) if group else None
            if ws is not None:
                return ws
            # Tag-less task with no usable default — pick first workspace
            # deterministically. Logged at debug for traceability.
            log(
                f"task {task.idx} ({task.name}) untagged; "
                f"using first workspace deterministically"
            )
            return next(iter(self.workspaces.values()))
        return self.workspace

    def set_tasks(self, tasks: dict[str, Any]):
        self.tasks.update(tasks)
        self.tasks_done.update({task_idx: asyncio.Event() for task_idx in tasks})
        self.remaining_tasks.update(set(tasks.keys()))

    def _all_tasks_done(self):
        return all(self.tasks_done[d].is_set() for d in self.tasks_done)

    def _get_all_executable_tasks(self):
        return [
            task_name
            for task_name in self.remaining_tasks
            if all(
                self.tasks_done[d].is_set() for d in self.tasks[task_name].dependencies
            )
        ]

    def _preprocess_args(self, task: Task):
        """Replace dependency placeholders, i.e. ${1}, in task.args with the actual observation."""
        args = []
        for arg in task.args:
            arg = _replace_arg_mask_with_real_value(arg, task.dependencies, self.tasks)
            args.append(arg)
        task.args = args

    async def _run_task(self, task: Task):
        self._preprocess_args(task)
        resolved = self._resolve_workspace(task)
        if resolved is not None:
            task.workspace = resolved
        if not task.is_join:
            observation = await task()
            task.observation = observation
        self.tasks_done[task.idx].set()

    async def schedule(self):
        """Run all tasks in self.tasks in parallel, respecting dependencies."""
        # run until all tasks are done
        while not self._all_tasks_done():
            # Find tasks with no dependencies or with all dependencies met
            executable_tasks = self._get_all_executable_tasks()

            for task_name in executable_tasks:
                asyncio.create_task(self._run_task(self.tasks[task_name]))
                self.remaining_tasks.remove(task_name)

            await asyncio.sleep(SCHEDULING_INTERVAL)

    async def aschedule(self, task_queue: asyncio.Queue[Optional[Task]], func):
        """Asynchronously listen to task_queue and schedule tasks as they arrive."""
        no_more_tasks = False  # Flag to check if all tasks are received

        while True:
            if not no_more_tasks:
                # Wait for a new task to be added to the queue
                task = await task_queue.get()

                # Check for sentinel value indicating end of tasks
                if task is None:
                    no_more_tasks = True
                else:
                    # Parse and set the new tasks
                    self.set_tasks({task.idx: task})

            # Schedule and run executable tasks
            executable_tasks = self._get_all_executable_tasks()

            if executable_tasks:
                for task_name in executable_tasks:
                    asyncio.create_task(self._run_task(self.tasks[task_name]))
                    self.remaining_tasks.remove(task_name)
            elif no_more_tasks and self._all_tasks_done():
                # Exit the loop if no more tasks are expected and all tasks are done
                break
            else:
                # If no executable tasks are found, sleep for the SCHEDULING_INTERVAL
                await asyncio.sleep(SCHEDULING_INTERVAL)