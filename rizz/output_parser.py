import ast
import csv
from io import StringIO
import re
from typing import Any, Optional, Sequence, Union

from langchain.agents.agent import AgentOutputParser
from langchain.schema import OutputParserException

from .task_fetching_unit import Task
from .base import StructuredTool, Tool

THOUGHT_PATTERN = r"Thought: ([^\n]*)"
# Phase 3 extension: optional `[wsA]` workspace tag before the index.
# Group order is now (ws, idx, tool, args, comment) — 5 groups.
ACTION_PATTERN = (
    r"\n*"
    r"(?:\[(\w+)\]\s*)?"
    r"(\d+)\. "
    r"(\w+)\((.*)\)"
    r"(\s*#\w+\n)?"
)
# $1 or ${1} -> 1
ID_PATTERN = r"\$\{?(\d+)\}?"

END_OF_PLAN = "<END_OF_PLAN>"


def default_dependency_rule(idx, args: str):
    matches = re.findall(ID_PATTERN, args)
    numbers = [int(match) for match in matches]
    return idx in numbers


class LLMCompilerPlanParser(AgentOutputParser, extra="allow"):
    """Planning output parser."""

    def __init__(self, tools: Sequence[Union[Tool, StructuredTool]], **kwargs):
        super().__init__(**kwargs)
        self.tools = tools

    def parse(self, text: str) -> list[str]:
        # 1. search("Ronaldo number of kids") -> 1, "search", '"Ronaldo number of kids"'
        # pattern = r"(\d+)\. (\w+)\(([^)]+)\)"
        pattern = rf"(?:{THOUGHT_PATTERN}\n)?{ACTION_PATTERN}"
        matches = re.findall(pattern, text)

        graph_dict = {}

        for match in matches:
            # match shape after Phase 3 ACTION_PATTERN extension:
            # (thought, ws, idx, tool, args, comment) — 6 elements total
            thought, ws, idx, tool_name, args, _ = match
            idx = int(idx)

            task = instantiate_task(
                tools=self.tools,
                idx=idx,
                tool_name=tool_name,
                args=args,
                thought=thought,
                workspace_group=ws or None,
            )

            graph_dict[idx] = task
            if task.is_join:
                break

        return graph_dict


### Helper functions


def _parse_llm_compiler_action_args(args: str) -> list[Any]:
    """Parse arguments from a string, handling special characters and quoted strings."""
    if args == "":
        return ()

    # Use csv.reader to split by commas, preserving quoted strings
    csv_reader = csv.reader(StringIO(args), skipinitialspace=True)
    parsed_args = next(csv_reader)

    # Convert each argument using ast.literal_eval if needed
    evaluated_args = []
    for arg in parsed_args:
        try:
            # Attempt to parse as Python literal (e.g., number, string, list)
            evaluated_args.append(ast.literal_eval(arg))
        except (ValueError, SyntaxError):
            # If not a valid Python literal, keep as string
            evaluated_args.append(arg)

    # Convert to tuple if only one argument
    if len(evaluated_args) == 1:
        return (evaluated_args[0],)

    return tuple(evaluated_args)


def _find_tool(
    tool_name: str, tools: Sequence[Union[Tool, StructuredTool]]
) -> Union[Tool, StructuredTool]:
    """Find a tool by name.

    Args:
        tool_name: Name of the tool to find.

    Returns:
        Tool or StructuredTool.
    """
    for tool in tools:
        if tool.name == tool_name:
            return tool
    raise OutputParserException(f"Tool {tool_name} not found.")


def _get_dependencies_from_graph(
    idx: int, tool_name: str, args: Sequence[Any]
) -> dict[str, list[str]]:
    """Get dependencies from a graph."""
    if tool_name == "join":
        # depends on the previous step
        dependencies = list(range(1, idx))
    else:
        # define dependencies based on the dependency rule in tool_definitions.py
        dependencies = [i for i in range(1, idx) if default_dependency_rule(i, args)]

    return dependencies


def instantiate_task(
    tools: Sequence[Union[Tool, StructuredTool]],
    idx: int,
    tool_name: str,
    args: str,
    thought: str,
    workspace_group: "Optional[str]" = None,
) -> Task:
    dependencies = _get_dependencies_from_graph(idx, tool_name, args)
    args = _parse_llm_compiler_action_args(args)
    if tool_name == "join":
        # join does not have a tool
        tool_func = lambda x: None
        stringify_rule = None
    else:
        tool = _find_tool(tool_name, tools)
        if hasattr(tool, 'coroutine') and tool.coroutine:
            tool_func = tool.coroutine
        else:
            tool_func = tool.func
        stringify_rule = tool.stringify_rule
    return Task(
        idx=idx,
        name=tool_name,
        tool=tool_func,
        args=args,
        dependencies=dependencies,
        stringify_rule=stringify_rule,
        thought=thought,
        is_join=tool_name == "join",
        workspace_group=workspace_group,
    )