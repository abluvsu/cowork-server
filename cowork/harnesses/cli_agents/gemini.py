"""Gemini CLI coworker — Google's free-tier CLI agent, headless via
`gemini -p <prompt> --yolo`.

Plain-text output, not the documented `--output-format json/stream`: as of
this writing there's an open upstream report that the structured JSON
output doesn't reliably match its own docs (google-gemini/gemini-cli#9009).
Rather than hardcode a parser against an unverified/unstable schema, this
follows the same conservative path Codex's harness takes for its own
unverified surface — every stdout line becomes a text_chunk, and
BaseCliHarness's generic accumulation fallback assembles final_text since
there's no structured completion marker.

supports_resume=False: Gemini CLI documents a `--resume` flag, but its
exact semantics (explicit conversation id vs. "resume last session") are
unconfirmed against a real install. Guessing wrong here risks the same
silent-hang failure mode Antigravity's resume showed — every turn starts
fresh until this is verified end-to-end.

supports_mcp=False for the same reason: Gemini CLI supports MCP servers,
but only via its own settings.json / extensions, not a documented
per-invocation flag — same situation as Codex (see codex.py), so left
unwired rather than guessed at.

--yolo is Gemini CLI's documented auto-approve-everything flag (the
skip_permissions_flag equivalent of Claude Code's --permission-mode
acceptEdits / Codex's --sandbox workspace-write); it cannot be defaulted
in Gemini's own settings.json, only passed per-invocation, which is
exactly the shape skip_permissions_flag exists for.
"""
from __future__ import annotations

import subprocess

from cowork.harnesses.base import register
from cowork.harnesses.cli_agents.base import BaseCliHarness
from cowork.harnesses.cli_agents.config import CliConfig
from cowork.harnesses.cli_agents.events import NormalizedEvent

GEMINI_CONFIG = CliConfig(
    executable="gemini",
    print_flag="-p",
    model_flag="--model",
    resume_flag=None,
    session_flag=None,
    skip_permissions_flag="--yolo",
    supports_resume=False,
    supports_images=False,
    supports_mcp=False,
)


@register
class GeminiHarness(BaseCliHarness):
    id = "gemini-cli"
    label = "Gemini CLI"
    config = GEMINI_CONFIG

    category = "CLI"
    priority = 8  # after the verified/higher-confidence CLIs
    tags = ("free-tier", "coding")

    @classmethod
    def available_models(cls) -> tuple[str, ...]:
        # Static catalog — no live `gemini models` discovery (unlike
        # Antigravity) since that command's presence/shape isn't
        # confirmed. Re-probe and switch to dynamic discovery once
        # verified against a real install; leaving empty means "let the
        # CLI use its own default" rather than risk a wrong hardcoded id.
        return ()

    def env_removals(self) -> list[str]:
        # Same rule as the other CLI coworkers: never let a stray API key
        # hijack billing away from the user's own Gemini CLI login.
        return ["GEMINI_API_KEY", "GOOGLE_API_KEY"]

    def parse_line(self, line: str) -> NormalizedEvent | None:
        return NormalizedEvent(type="text_chunk", text=line)

    def check_status(self) -> dict:
        # No confirmed login/whoami subcommand — same situation as
        # Antigravity. `gemini --version` succeeding is only proof the
        # binary runs, not that it's authenticated, so loggedIn stays
        # unset (None) rather than asserting something unverified.
        base = super().check_status()
        if not base["installed"]:
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
            "Login status isn't checked — run `gemini` directly to confirm you're signed in."
        )
        return base
