"""Claude Code coworker — the CLI's own stream-json event shape mapped
onto NormalizedEvent. All subprocess/lifecycle/retry plumbing lives in
BaseCliHarness; this class only knows Claude Code's specific flags and
wire format.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from cowork.harnesses.base import register
from cowork.harnesses.cli_agents.base import BaseCliHarness, _enabled_mcp_servers
from cowork.harnesses.cli_agents.config import CliConfig
from cowork.harnesses.cli_agents.events import ConversationRequest, NormalizedEvent

CLAUDE_CONFIG = CliConfig(
    executable="claude",
    print_flag="-p",
    model_flag="--model",
    resume_flag="--resume",
    session_flag="--session-id",
    skip_permissions_flag=None,  # uses --permission-mode instead — see build_arguments
    supports_resume=True,
    supports_images=False,  # pass file paths in the prompt instead
    supports_mcp=True,
    mcp_config_flag="--mcp-config",
)


@register
class ClaudeCodeHarness(BaseCliHarness):
    id = "claude-code"
    label = "Claude Code"
    config = CLAUDE_CONFIG

    category = "CLI"
    priority = 5  # fast, no per-token metering against a free API tier
    tags = ("subscription", "fast", "coding", "mcp")

    @classmethod
    def available_models(cls) -> tuple[str, ...]:
        # The aliases the CLI documents for --model (verified against
        # claude 2.1.198: `--model sonnet` / `--model haiku` both work).
        # Aliases track "latest of that family" so they never go stale
        # the way full model ids (claude-fable-5, …) would.
        return ("fable", "opus", "sonnet", "haiku")

    def preferred_paths(self) -> tuple[str, ...]:
        # The native Claude Code installer always writes here. Checked
        # before a PATH scan so an unrelated `npm i -g` shim elsewhere
        # on PATH (a stale/different claude install, e.g. the deprecated
        # @anthropic-ai/claude-code package) can never shadow it — bit
        # us for real: adding npm's global bin to PATH for Codex support
        # made shutil.which('claude') resolve a months-old npm shim
        # instead, breaking auth-status and turns for the default coworker.
        bin_dir = Path.home() / ".local" / "bin"
        return (str(bin_dir / "claude.exe"), str(bin_dir / "claude"))

    def check_status(self) -> dict:
        base = super().check_status()
        if not base["installed"]:
            return base
        try:
            # 30s not 15s: CLI cold start under system load was observed
            # exceeding 15s, turning a logged-in install into a spurious
            # "could not read auth status" in the CLI Agents panel.
            result = subprocess.run(
                [*self.spawn_argv(base["path"]), "auth", "status"],
                capture_output=True, text=True, timeout=30,
            )
            status = json.loads(result.stdout)
        except Exception as exc:
            base["detail"] = f"Could not read auth status: {exc}"
            return base
        base["loggedIn"] = bool(status.get("loggedIn"))
        base["account"] = status.get("email")
        base["plan"] = status.get("subscriptionType")
        base["detail"] = (
            f"Logged in as {status.get('email')} ({status.get('subscriptionType')})"
            if base["loggedIn"] else "Not logged in — run `claude auth login`."
        )
        return base

    def build_arguments(self, request: ConversationRequest, *, resume: bool) -> list[str]:
        args = super().build_arguments(request, resume=resume)
        # stream-json in -p mode requires --verbose; --permission-mode
        # acceptEdits auto-approves in-project file edits per the user's
        # decision (grill-me, 2026-07-03) rather than hanging on a
        # prompt no terminal exists to answer.
        args += ["--output-format", "stream-json", "--verbose", "--permission-mode", "acceptEdits"]
        # acceptEdits only covers file edits — MCP tool calls (mcp__<id>__*)
        # still hit the interactive permission system, and there's no TTY
        # here to answer the prompt, so without this every MCP call is
        # silently denied (grill-me, 2026-07-11: personal single-user app,
        # consent already happened when the user enabled the server in
        # Settings -> MCP Servers, so a second CLI-level prompt is dead
        # weight, not a safety backstop). Scoped per-server, not blanket
        # bypassPermissions, so Bash/other tools still prompt normally.
        mcp_servers = _enabled_mcp_servers()
        if mcp_servers:
            patterns = [f"mcp__{s['id']}__*" for s in mcp_servers]
            args += ["--allowedTools", ",".join(patterns)]
        return args

    def env_removals(self) -> list[str]:
        # Never let a stray API key hijack billing away from the
        # user's subscription login.
        return ["ANTHROPIC_API_KEY"]

    def parse_line(self, line: str) -> NormalizedEvent | None:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return None
        etype = event.get("type")

        if etype == "assistant":
            for block in (event.get("message") or {}).get("content") or []:
                btype = block.get("type")
                if btype == "text" and block.get("text"):
                    return NormalizedEvent(type="text_chunk", text=block["text"])
                if btype == "tool_use":
                    return NormalizedEvent(
                        type="tool_call",
                        tool_call_id=block.get("id", ""),
                        tool_name=block.get("name", "tool"),
                        tool_args=block.get("input"),
                    )
            return None

        if etype == "user":
            for block in (event.get("message") or {}).get("content") or []:
                if block.get("type") == "tool_result":
                    content = block.get("content")
                    if isinstance(content, list):
                        content = " ".join(c.get("text", "") for c in content if isinstance(c, dict))
                    return NormalizedEvent(
                        type="tool_result",
                        tool_call_id=block.get("tool_use_id", ""),
                        tool_result=str(content or "")[:65536],
                    )
            return None

        if etype == "result":
            return NormalizedEvent(type="completed", final_text=event.get("result") or "")

        return None
