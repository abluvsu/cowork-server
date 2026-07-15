"""OpenCode coworker — the open-source multi-provider CLI agent, headless
via `opencode run <prompt>`.

Confirmed flags (opencode.ai/docs/cli, fetched 2026-07): `opencode run
[message..]` (positional prompt), `--model`/`-m` (format `provider/model`
— unlike the other CLIs' bare model names, so available_models() is left
empty rather than guess at a user's configured provider/model pair),
`--auto` (auto-approve permissions not explicitly denied — the
skip_permissions_flag equivalent), `--session <id>`/`--continue` for
session control, `--format json|default`.

Plain-text output, not `--format json`: the JSON event schema isn't
documented in enough detail to parse reliably (same situation as Gemini
CLI — see gemini.py). Every stdout line becomes a text_chunk; the
generic accumulation fallback assembles final_text.

supports_resume=False: OpenCode's session flags don't map cleanly onto
cowork's model. `--continue` resumes whatever was last used process-wide
(no explicit id), and `--session <id>` docs don't confirm whether an
unrecognized id auto-creates a fresh session or errors — either way,
naively passing cowork's conversation UUID risks resuming the WRONG
conversation's session (cross-conversation bleed) rather than starting
fresh. Left off until verified end-to-end, same precedent as
Antigravity/Codex's unverified resume paths.

supports_mcp=True (OpenCode genuinely supports MCP) but mcp_config_flag
is None: `opencode run` has no per-invocation MCP config flag — servers
are added via `opencode mcp add` into OpenCode's own config file, the
same situation as Codex (see codex.py's docstring for the reasoning).

Auth: `opencode auth login` stores provider API keys in
~/.local/share/opencode/auth.json, not a single subscription-account env
var the way Claude Code/Codex/Gemini CLI work — there's no one stray key
to strip via env_removals() the way the other harnesses do.
"""
from __future__ import annotations

import subprocess

from cowork.harnesses.base import register
from cowork.harnesses.cli_agents.base import BaseCliHarness
from cowork.harnesses.cli_agents.config import CliConfig
from cowork.harnesses.cli_agents.events import NormalizedEvent

OPENCODE_CONFIG = CliConfig(
    executable="opencode",
    print_flag="run",  # subcommand, not a flag — prepended before the prompt just the same
    model_flag="--model",
    resume_flag=None,
    session_flag=None,
    skip_permissions_flag="--auto",
    supports_resume=False,
    supports_images=False,
    supports_mcp=True,
    mcp_config_flag=None,
)


@register
class OpenCodeHarness(BaseCliHarness):
    id = "opencode"
    label = "OpenCode"
    config = OPENCODE_CONFIG

    category = "CLI"
    priority = 9
    tags = ("open-source", "multi-provider", "coding")

    @classmethod
    def available_models(cls) -> tuple[str, ...]:
        # OpenCode's --model takes "provider/model" (e.g.
        # "anthropic/claude-sonnet-4-6"), keyed to whichever providers the
        # user ran `opencode auth login` for — not a fixed catalog this
        # harness can hardcode. Empty means "let the CLI use its own
        # configured default" rather than guess at a user's provider mix.
        return ()

    def parse_line(self, line: str) -> NormalizedEvent | None:
        return NormalizedEvent(type="text_chunk", text=line)

    def check_status(self) -> dict:
        # No confirmed login/whoami subcommand distinct from `auth login`
        # itself — same situation as Gemini CLI. loggedIn stays unset
        # (None) rather than asserting something unverified.
        base = super().check_status()
        if not base["installed"]:
            base["detail"] = "Not installed — run `opencode auth login` after installing to configure a provider."
            return base
        try:
            result = subprocess.run(
                [*self.spawn_argv(base["path"]), "--version"],
                capture_output=True, text=True, timeout=15,
            )
        except Exception as exc:
            base["detail"] = f"Could not run '{self.config.executable} --version': {exc}"
            return base
        base["detail"] = (
            f"Installed ({result.stdout.strip() or result.stderr.strip()}). "
            "Login status isn't checked — run `opencode auth login` to confirm a provider is configured."
        )
        return base
