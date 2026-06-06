"""Verify the `ToolGenerator` direct-instance / direct-class paths.

Before the fix: `ToolGenerator` only resolved entries whose `class` was a
*string* matching a `*Tool`-suffixed class discovered by folder scan. That
made it impossible to register `ClaudeCodeAgent` (or any `CodingAgent`)
from a `--tools-config` file.

After the fix: an entry can be either
  - {"instance": <CodingAgent>}  -> uses the already-built object
  - {"class": SomeClass, ...}    -> instantiates with kwargs
in addition to the legacy {"class": "SomeStringName"} discovery path.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rizz.agents.agent_result import AgentResult  # noqa: E402
from rizz.agents.base_agent import CodingAgent  # noqa: E402
from rizz.tools.tool_generator import ToolGenerator  # noqa: E402


class StubAgent(CodingAgent):
    async def run(self, goal, workspace):
        return AgentResult(summary=f"{self.name}: {goal}", exit_status="ok")


def main() -> None:
    instance_tools = ToolGenerator(
        [{"instance": StubAgent("impl", "Implements features")}],
    )
    assert len(instance_tools) == 1, instance_tools
    assert instance_tools[0].name == "impl"
    print("instance path: OK")

    class_tools = ToolGenerator(
        [{"class": StubAgent, "name": "tests", "description": "Adds tests"}],
    )
    assert len(class_tools) == 1, class_tools
    assert class_tools[0].name == "tests"
    print("class-object path: OK")

    mixed = ToolGenerator(
        [
            {"instance": StubAgent("docs", "Updates docs")},
            {"class": StubAgent, "name": "lint", "description": "Runs lint"},
            {"class": "NonexistentTool", "name": "ignored"},
        ],
    )
    assert len(mixed) == 2, mixed
    names = {t.name for t in mixed}
    assert names == {"docs", "lint"}, names
    print("mixed config: OK")

    print("tool_generator_smoke: PASS")


if __name__ == "__main__":
    main()
