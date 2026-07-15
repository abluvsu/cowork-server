"""MCP wiring into live CLI turns (base.py:_build_mcp_arguments).

Settings -> MCP servers (mcp_servers_json) should only be injected for a
CLI that both declares supports_mcp and has a mcp_config_flag (a
per-invocation JSON-config flag, e.g. Claude Code's --mcp-config). A CLI
with supports_mcp=True but no mcp_config_flag (e.g. Codex, which has no
per-run flag) must never get the flag appended — see codex.py's
docstring for why that's deliberate, not an oversight.
"""
import json

import pytest

from cowork.harnesses.cli_agents.base import BaseCliHarness
from cowork.harnesses.cli_agents.config import CliConfig
from cowork.harnesses.cli_agents.events import ConversationRequest


class _McpCli(BaseCliHarness):
    id = "mcp-cli"
    label = "MCP CLI"
    config = CliConfig(executable="mcp-cli", supports_mcp=True, mcp_config_flag="--mcp-config")


class _NoFlagMcpCli(BaseCliHarness):
    # Mirrors Codex: supports_mcp=True (real capability) but no
    # per-invocation flag — must never get --mcp-config appended.
    id = "no-flag-cli"
    label = "No Flag CLI"
    config = CliConfig(executable="no-flag-cli", supports_mcp=True, mcp_config_flag=None)


class _NoMcpCli(BaseCliHarness):
    id = "no-mcp-cli"
    label = "No MCP CLI"
    config = CliConfig(executable="no-mcp-cli", supports_mcp=False)


def _request() -> ConversationRequest:
    return ConversationRequest(conversation_id="c1", prompt="hi", cwd=".", profile={}, resume=False)


def _set_mcp_servers(monkeypatch, servers: list[dict]) -> None:
    from cowork.common.settings import user_settings as us_module

    settings = us_module.UserSettings(mcp_servers_json=json.dumps(servers))
    monkeypatch.setattr(us_module, "get_user_settings", lambda: settings)


def test_no_flag_when_no_servers_configured(monkeypatch):
    _set_mcp_servers(monkeypatch, [])
    args = _McpCli().build_arguments(_request(), resume=False)
    assert "--mcp-config" not in args


def test_flag_appended_with_enabled_servers(monkeypatch):
    _set_mcp_servers(monkeypatch, [
        {"id": "fs", "enabled": True, "command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem"], "env": {}},
        {"id": "disabled-one", "enabled": False, "command": "whatever"},
    ])
    args = _McpCli().build_arguments(_request(), resume=False)
    assert "--mcp-config" in args
    path = args[args.index("--mcp-config") + 1]
    payload = json.loads(open(path, encoding="utf-8").read())
    assert list(payload["mcpServers"].keys()) == ["fs"]
    assert payload["mcpServers"]["fs"]["command"] == "npx"


def test_malformed_entries_are_skipped(monkeypatch):
    _set_mcp_servers(monkeypatch, [
        {"id": "missing-command"},
        "not-a-dict",
        {"command": "missing-id"},
        {"id": "ok", "command": "real-cmd"},
    ])
    args = _McpCli().build_arguments(_request(), resume=False)
    assert "--mcp-config" in args
    path = args[args.index("--mcp-config") + 1]
    payload = json.loads(open(path, encoding="utf-8").read())
    assert list(payload["mcpServers"].keys()) == ["ok"]


def test_no_flag_when_cli_has_no_mcp_config_flag(monkeypatch):
    # Codex's exact shape: supports_mcp=True, mcp_config_flag=None.
    _set_mcp_servers(monkeypatch, [{"id": "fs", "command": "npx"}])
    args = _NoFlagMcpCli().build_arguments(_request(), resume=False)
    assert "--mcp-config" not in args


def test_no_flag_when_cli_does_not_support_mcp(monkeypatch):
    _set_mcp_servers(monkeypatch, [{"id": "fs", "command": "npx"}])
    args = _NoMcpCli().build_arguments(_request(), resume=False)
    assert "--mcp-config" not in args


class TestClaudeCodeAllowedTools:
    """Claude Code's --permission-mode acceptEdits only auto-approves
    in-project file edits — MCP tool calls still hit the interactive
    permission system, and there's no TTY in this headless subprocess to
    answer that prompt. Without --allowedTools, every mcp__<id>__* call is
    silently denied (reproduced live 2026-07-11: gmail MCP wired and
    reachable, but search_emails came back "blocked by permissions").
    """

    def test_allowed_tools_appended_for_enabled_servers(self, monkeypatch):
        from cowork.harnesses.cli_agents.claude_code import ClaudeCodeHarness

        _set_mcp_servers(monkeypatch, [
            {"id": "gmail", "enabled": True, "command": "npx", "args": [], "env": {}},
            {"id": "whatsapp", "enabled": True, "command": "npx", "args": [], "env": {}},
            {"id": "disabled-one", "enabled": False, "command": "whatever"},
        ])
        args = ClaudeCodeHarness().build_arguments(_request(), resume=False)
        assert "--allowedTools" in args
        patterns = args[args.index("--allowedTools") + 1]
        assert patterns == "mcp__gmail__*,mcp__whatsapp__*"

    def test_no_allowed_tools_flag_when_no_servers_configured(self, monkeypatch):
        from cowork.harnesses.cli_agents.claude_code import ClaudeCodeHarness

        _set_mcp_servers(monkeypatch, [])
        args = ClaudeCodeHarness().build_arguments(_request(), resume=False)
        assert "--allowedTools" not in args


def test_antigravity_print_timeout_extended_past_its_5m_default(monkeypatch):
    # agy's own --print-timeout defaults to 5m and it reconnects to its
    # backend on a cold start - reproduced live: a real turn hit agy's own
    # "Error: timeout waiting for response" at the 5m mark. 14m keeps agy's
    # deadline just under our own 900s (15m) TURN_TIMEOUT_SECONDS.
    from cowork.harnesses.cli_agents.antigravity import AntigravityHarness

    _set_mcp_servers(monkeypatch, [])
    args = AntigravityHarness().build_arguments(_request(), resume=False)
    assert "--print-timeout" in args
    assert args[args.index("--print-timeout") + 1] == "14m"
