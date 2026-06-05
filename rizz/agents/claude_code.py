"""Phase 2 — Claude Agent SDK backend.

Drives `claude_agent_sdk.query()` against `workspace.cwd`, then auto-commits
any changes so subsequent agents in the DAG inherit committed state and the
joiner has a stable diff to reason over.

The SDK is imported lazily so this module imports cleanly even when
`claude-agent-sdk` isn't installed (the smoke test uses a stub that doesn't
need the SDK at all).

Auth: reads `ANTHROPIC_API_KEY` from the environment per the SDK's contract.
The key is never logged or written to commits/notes. Transcripts are
opt-in (`capture_transcript=False` by default) for the same reason.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING, List, Optional

from .agent_result import AgentResult
from .base_agent import CodingAgent

if TYPE_CHECKING:
    from ..workspace import Workspace

log = logging.getLogger(__name__)

DEFAULT_COMMIT_TEMPLATE = "[rizz/{wsid}] {agent_name}: {short_goal}"


class ClaudeCodeAgent(CodingAgent):
    def __init__(
        self,
        name: str,
        description: str,
        *,
        model: Optional[str] = None,
        api_key: Optional[str] = None,
        commit_message_template: str = DEFAULT_COMMIT_TEMPLATE,
        auto_commit: bool = True,
        no_verify: bool = False,
        timeout_seconds: int = 600,
        capture_transcript: bool = False,
        permission_mode: str = "acceptEdits",
        system_prompt: Optional[str] = None,
        allowed_tools: Optional[List[str]] = None,
    ):
        super().__init__(name=name, description=description)
        self._model = model
        self._api_key = api_key
        self._commit_template = commit_message_template
        self._auto_commit = auto_commit
        self._no_verify = no_verify
        self._timeout = timeout_seconds
        self._capture = capture_transcript
        self._permission_mode = permission_mode
        self._system_prompt = system_prompt
        self._allowed_tools = allowed_tools

    async def run(self, goal: str, workspace: "Workspace") -> AgentResult:
        # 1. Auth
        api_key = self._api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return AgentResult(
                summary=f"{self.name}: ANTHROPIC_API_KEY missing",
                exit_status="error",
                error="ANTHROPIC_API_KEY not set",
                branch=workspace.branch,
            )

        # 2. Snapshot HEAD before so we can attribute new commits.
        head_before: Optional[str] = None
        if workspace.repo_root is not None:
            try:
                head_before = (await workspace.run_git("rev-parse", "HEAD")).strip()
            except Exception as e:
                log.warning("could not snapshot HEAD before agent run: %s", e)

        transcript_chunks: List[str] = []
        exit_status = "ok"
        error: Optional[str] = None

        # 3. Drive the SDK.
        try:
            from claude_agent_sdk import query, ClaudeAgentOptions  # type: ignore

            options_kwargs = {
                "cwd": str(workspace.cwd),
                "permission_mode": self._permission_mode,
            }
            if self._model is not None:
                options_kwargs["model"] = self._model
            if self._system_prompt is not None:
                options_kwargs["system_prompt"] = self._system_prompt
            if self._allowed_tools is not None:
                options_kwargs["allowed_tools"] = self._allowed_tools
            # The SDK reads ANTHROPIC_API_KEY from the env automatically. If
            # the caller passed an explicit api_key, surface it through
            # options.env so we never mutate the parent process's env.
            if self._api_key is not None:
                options_kwargs["env"] = {"ANTHROPIC_API_KEY": self._api_key}

            options = ClaudeAgentOptions(**options_kwargs)

            async def _drive() -> None:
                async for msg in query(prompt=goal, options=options):
                    if self._capture:
                        transcript_chunks.append(str(msg))

            await asyncio.wait_for(_drive(), timeout=self._timeout)
        except asyncio.TimeoutError:
            exit_status = "timeout"
            error = f"agent exceeded {self._timeout}s"
            log.warning("%s timed out after %ss", self.name, self._timeout)
        except ImportError as e:
            return AgentResult(
                summary=f"{self.name}: claude-agent-sdk not installed",
                exit_status="error",
                error=repr(e),
                branch=workspace.branch,
            )
        except Exception as e:
            exit_status = "error"
            error = repr(e)
            log.exception("ClaudeCodeAgent.run failed")

        # 4. Auto-commit any changes the agent made (even on partial failure
        #    so the joiner can still see what happened).
        new_commits: List[str] = []
        if self._auto_commit and workspace.repo_root is not None:
            try:
                from .. import git_utils

                if await git_utils.is_dirty(workspace.cwd):
                    short = goal.replace("\n", " ").strip()
                    short = short[:64] + ("..." if len(short) > 64 else "")
                    msg = self._commit_template.format(
                        wsid=workspace.id,
                        agent_name=self.name,
                        short_goal=short,
                    )
                    await workspace.run_git("add", "-A")
                    commit_args: List[str] = ["commit", "-m", msg]
                    if self._no_verify:
                        commit_args.append("--no-verify")
                    await workspace.run_git(*commit_args)
                    head_after = (
                        await workspace.run_git("rev-parse", "HEAD")
                    ).strip()
                    if head_before and head_after != head_before:
                        rng = f"{head_before}..{head_after}"
                        out = await workspace.run_git("rev-list", rng)
                        new_commits = [s for s in out.splitlines() if s.strip()]
                else:
                    if exit_status == "ok":
                        exit_status = "no_changes"
            except Exception as e:
                log.warning("auto-commit failed: %s", e)
                if exit_status == "ok":
                    exit_status = "error"
                    error = f"auto-commit failed: {e!r}"

        # 5. Summary: prefer the agent's last message, else fall back to goal.
        if transcript_chunks:
            first_line = transcript_chunks[-1].splitlines()[0] if transcript_chunks[-1] else ""
            summary = first_line[:500] or f"completed: {goal[:160]}"
        else:
            summary = f"completed: {goal[:160]}"

        return await AgentResult.from_workspace(
            workspace,
            summary=summary,
            new_commits=new_commits,
            transcript="\n".join(transcript_chunks) if self._capture else None,
            exit_status=exit_status,
            error=error,
        )
