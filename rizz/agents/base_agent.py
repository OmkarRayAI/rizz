"""Phase 2 — the `CodingAgent` ABC and the StructuredTool wrapper.

A CodingAgent represents one backend (Claude Agent SDK, Codex, aider, an
in-house ReAct loop, etc.). Subclasses implement `run(goal, workspace)` and
return an `AgentResult`. The base class wraps any agent into a
`StructuredTool` whose coroutine signature is `(goal: str, *, workspace)` —
which lights up Phase 1's `_accepts_workspace` injection automatically and
makes the agent indistinguishable from a regular tool to the planner.

The planner sees a single `goal: str` parameter (via the explicit
`args_schema=_AgentGoal`), keeping the locked planner UX:
  `1. fix_login_bug("Resolve the 401 in /login when ...")`
"""

from __future__ import annotations

import abc
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from ..base import StructuredTool
from .agent_result import AgentResult

if TYPE_CHECKING:
    from ..workspace import Workspace


class _AgentGoal(BaseModel):
    """Single-field schema shared by every coding-agent tool."""

    goal: str = Field(..., description="The natural-language goal for this agent.")


class CodingAgent(abc.ABC):
    """Abstract base for coding-agent backends."""

    name: str
    description: str

    def __init__(self, name: str, description: str):
        self.name = name
        self.description = description

    @abc.abstractmethod
    async def run(self, goal: str, workspace: "Workspace") -> AgentResult:
        """Execute the agent against the given workspace and return its result."""

    def get_tool(self) -> StructuredTool:
        """Wrap this agent so it can be registered alongside ordinary tools."""
        return agent_to_structured_tool(self)


def agent_to_structured_tool(agent: CodingAgent) -> StructuredTool:
    async def _run(goal: str, workspace=None) -> AgentResult:
        if workspace is None:
            return AgentResult(
                summary=f"{agent.name}: no workspace allocated",
                exit_status="error",
                error=(
                    "Coding agents require a Workspace. "
                    "Pass repo= to Rizz.run()."
                ),
            )
        return await agent.run(goal, workspace)

    return StructuredTool.from_function(
        coroutine=_run,
        name=agent.name,
        description=agent.description,
        args_schema=_AgentGoal,
    )
