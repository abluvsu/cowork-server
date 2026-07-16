"""BaseCliHarness — owns everything generic about driving a headless CLI
coding agent as a cowork coworker: subprocess spawn, stream reading,
timeouts, the resume-then-retry-fresh fallback, env sanitization, and
translating normalized events into the transport shape the existing SSE
formatter expects.

A concrete coworker (ClaudeCodeHarness, a future AntigravityHarness/
CodexHarness) supplies only:
  - `config`: a CliConfig (pure data — executable, flags)
  - `parse_line`: the CLI's own wire format -> NormalizedEvent
  - `env_overrides`/`env_removals`: only if the CLI needs env changes
  - `build_arguments`: only if the CLI's flag shape genuinely diverges
    from the common print/resume/model pattern (the default here already
    covers Claude Code and Antigravity as-is)

Why a subprocess instead of an API call: this spawns the user's own
logged-in CLI exactly as a terminal would — it does not extract or
reuse OAuth tokens against the API. Session continuity is delegated
entirely to the CLI's own conversation state, keyed by the cowork
conversation UUID.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

from cowork.common.logger import get_logger
from cowork.harnesses.base import FileInputBlock, TextInputBlock
from cowork.schemas.memory import MemoryScope
from cowork.harnesses.cli_agents.config import CliConfig
from cowork.harnesses.cli_agents.events import ConversationRequest, NormalizedEvent
from cowork.harnesses.hermes_harness.stream_formatter import format_hermes_stream
from cowork.models.conversation import Conversation
from cowork.models.project import Project
from cowork.models.skill import Skill

logger = get_logger(__name__)

TURN_TIMEOUT_SECONDS = 900

# Where per-invocation MCP config files are written. The app profile dir
# (~/.cowork), NOT the project working directory — these files can embed
# server env vars (API keys/tokens for the MCP server itself), so they
# must not land inside a user's project folder where they could be
# committed or synced.
_MCP_CONFIG_DIR = Path.home() / ".cowork" / "mcp-run"


def _enabled_mcp_servers() -> list[dict]:
    """Enabled servers from Settings → MCP servers (mcp_servers_json),
    reshaped to the `{command, args, env}` triple CLIs expect. Defensive
    like every other *_json settings field in this codebase — malformed
    entries are skipped rather than failing the whole turn.
    """
    from cowork.common.settings.user_settings import get_user_settings

    try:
        raw = json.loads(get_user_settings().mcp_servers_json or "[]")
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(raw, list):
        return []

    servers = []
    for entry in raw:
        if not isinstance(entry, dict) or not entry.get("enabled", True):
            continue
        server_id = entry.get("id")
        command = entry.get("command")
        if not server_id or not command:
            continue
        servers.append({
            "id": str(server_id),
            "command": str(command),
            "args": [str(a) for a in (entry.get("args") or [])],
            "env": {str(k): str(v) for k, v in (entry.get("env") or {}).items()},
        })
    return servers


def _write_mcp_config_file(servers: list[dict]) -> Path:
    """Write the `{"mcpServers": {...}}` file Claude Code's --mcp-config
    (and compatible CLIs) expect. Filename is content-hashed so identical
    server sets across turns reuse the same file instead of accumulating
    one file per turn, while a server-set change gets its own file
    (no risk of a stale in-flight turn reading a file another turn just
    overwrote).
    """
    mcp_servers = {
        s["id"]: {"command": s["command"], "args": s["args"], "env": s["env"]}
        for s in servers
    }
    payload = json.dumps({"mcpServers": mcp_servers}, sort_keys=True)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    _MCP_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    path = _MCP_CONFIG_DIR / f"{digest}.json"
    if not path.exists():
        path.write_text(payload, encoding="utf-8")
    return path


class BaseCliHarness:
    config: CliConfig
    # Every CLI harness's normalized events map onto the same dict shapes
    # HermesHarness yields (see _legacy_dict below), so its formatter is
    # reused as-is rather than adding a second SSE formatter.
    formatter = staticmethod(format_hermes_stream)

    category = "CLI"
    priority = 100
    tags: tuple[str, ...] = ()

    @classmethod
    def available_models(cls) -> tuple[str, ...]:
        """Static model catalog for the schema's model-picker, if this CLI
        has a fixed list worth surfacing. Empty means 'let the CLI use its
        own default' — no model-picker control is included."""
        return ()

    @classmethod
    def configuration_schema(cls) -> list[dict]:
        schema: list[dict] = []
        if cls.config.model_flag and cls.available_models():
            schema.append({"type": "model-picker", "id": "model", "options": list(cls.available_models())})
        if cls.config.skip_permissions_flag:
            schema.append({"type": "checkbox", "id": "skipPermissions", "label": "Skip permission prompts"})
        schema.append({"type": "directory", "id": "workingDirectory", "label": "Working directory"})
        return schema

    # ── No-op memory/skills protocol — the CLI manages its own ──────────

    async def sync_skills(self, skills: list[Skill]) -> None:
        return None

    async def overwrite_memory(self, scope: MemoryScope, category: str, content: str, project: Project | None = None) -> None:
        raise ValueError(f"{self.label} manages its own memory; not editable from cowork.")

    async def retrieve_memory(self, scope: MemoryScope, category: str, project: Project | None = None) -> str:
        return ""

    async def delete_memory(self, scope: MemoryScope, category: str, project: Project | None = None) -> None:
        raise ValueError(f"{self.label} manages its own memory; not editable from cowork.")

    async def list_memory(self, projects: list[Project]) -> list:
        return []

    # ── Behavior hooks subclasses implement ──────────────────────────────

    def preferred_paths(self) -> tuple[str, ...]:
        """Canonical install locations to check before falling back to a
        PATH scan. Override when this CLI's own installer always writes
        to a well-known location — otherwise an unrelated same-named
        tool earlier on PATH (e.g. an old `npm i -g` shim) can silently
        shadow the real binary the moment PATH gets reordered for any
        reason (a different launch mechanism, a PATH change made for a
        completely different coworker)."""
        return ()

    @classmethod
    def search_path(cls) -> str:
        """PATH used for CLI discovery and spawning, independent of how
        the server process was launched. The Electron app, a service
        wrapper, and a terminal all hand the server different PATHs —
        coworkers must not appear/disappear based on that accident, so
        the user-level dirs where coworker CLIs actually install are
        appended when missing."""
        base = os.environ.get("PATH", "")
        if os.name != "nt":
            return base
        extras = (
            os.path.expandvars(r"%USERPROFILE%\.local\bin"),
            os.path.expandvars(r"%APPDATA%\npm"),
            os.path.expandvars(r"%LOCALAPPDATA%\agy\bin"),
        )
        parts = base.split(os.pathsep) if base else []
        seen = {p.lower() for p in parts}
        for extra in extras:
            if extra.lower() not in seen and os.path.isdir(extra):
                parts.append(extra)
        return os.pathsep.join(parts)

    def find_cli(self) -> str | None:
        for candidate in self.preferred_paths():
            if os.path.isfile(candidate):
                return candidate
        return shutil.which(self.config.executable, path=self.search_path())

    @staticmethod
    def spawn_argv(cli: str) -> list[str]:
        """argv prefix that can actually execute `cli` on this platform.
        npm-installed CLIs on Windows resolve to `.cmd`/`.bat` shims,
        which CreateProcess can't exec directly (WinError 193) — those
        must be routed through cmd.exe."""
        if os.name == "nt" and cli.lower().endswith((".cmd", ".bat")):
            return ["cmd.exe", "/c", cli]
        return [cli]

    def check_status(self) -> dict:
        """Installed/login status for the Settings "CLI Agents" panel.

        Default: install-check only (`loggedIn: None` means "not checked" —
        most CLIs don't expose a login-status command at all). Override in
        a subclass for a CLI that does (e.g. `claude auth status`); this is
        genuinely per-CLI behavior, not something the base class can guess
        at generically.
        """
        path = self.find_cli()
        if path is None:
            return {"installed": False, "path": None, "loggedIn": None,
                     "detail": f"'{self.config.executable}' not found on PATH."}
        return {"installed": True, "path": path, "loggedIn": None, "detail": ""}

    def should_resume(self, messages) -> bool:
        """The conversation-execution-isolation invariant: a CLI coworker
        resumes its native session ONLY if IT produced a prior assistant
        turn in this conversation. It never resumes on another coworker's
        turns (chat history is a shared UI artifact; native sessions are
        per-coworker). Pure function of the message list so it's unit-
        testable without spawning the subprocess."""
        if not self.config.supports_resume:
            return False
        return any(
            getattr(m, "role", None) == "assistant" and getattr(m, "harness", None) == self.id
            for m in messages
        )

    def build_arguments(self, request: ConversationRequest, *, resume: bool) -> list[str]:
        """Default arg builder for the common print/resume/model shape.
        Override for a CLI whose flags diverge further."""
        cfg = self.config
        args = [cfg.print_flag, request.prompt]
        if resume and cfg.resume_flag:
            args += [cfg.resume_flag, request.conversation_id]
        elif not resume and cfg.session_flag:
            args += [cfg.session_flag, request.conversation_id]
        # A coworker id arriving as the "model" is a picker-selection leak
        # (CLI coworkers reuse their harness id as the option id) — the CLI
        # would reject `--model claude-code` and stream the rejection back
        # as the reply. Fall back to the CLI's own default model instead.
        model = request.profile.get("model")
        if cfg.model_flag and model and model != self.id:
            args += [cfg.model_flag, model]
        if cfg.skip_permissions_flag and request.profile.get("skipPermissions", True):
            args.append(cfg.skip_permissions_flag)
        mcp_args = self._build_mcp_arguments()
        return [*args, *cfg.default_args, *mcp_args]

    def _build_mcp_arguments(self) -> list[str]:
        """Inject enabled MCP servers (Settings → MCP) into this turn, for
        CLIs that take a per-invocation JSON config file (config.mcp_config_flag).
        No-op when the CLI doesn't support that (declared via
        mcp_config_flag=None — e.g. Codex reads MCP servers from its own
        config.toml, not per-invocation) or no servers are enabled.
        """
        cfg = self.config
        if not cfg.supports_mcp or not cfg.mcp_config_flag:
            return []
        servers = _enabled_mcp_servers()
        if not servers:
            return []
        path = _write_mcp_config_file(servers)
        return [cfg.mcp_config_flag, str(path)]

    def parse_line(self, line: str) -> NormalizedEvent | None:
        """Required override: one line of the CLI's stdout -> NormalizedEvent,
        or None to skip it. A `NormalizedEvent(type="completed", final_text=...)`
        marks the CLI's own final-result line."""
        raise NotImplementedError

    def env_overrides(self) -> dict[str, str]:
        return {}

    def env_removals(self) -> list[str]:
        """Env vars to strip before spawning — e.g. an API key that would
        otherwise hijack billing away from the user's subscription login."""
        return []

    # ── Chat turn ────────────────────────────────────────────────────────

    async def stream_response(
        self,
        *,
        conversation: Conversation,
        input: list[TextInputBlock | FileInputBlock],
        model: str | None = None,
        disabled_connections: list[dict] | None = None,
    ) -> AsyncIterator[dict]:
        cli = self.find_cli()
        if cli is None:
            yield self._legacy_dict(NormalizedEvent(
                type="error",
                detail=f"{self.label} CLI ('{self.config.executable}') is not installed or not on PATH.",
            ))
            yield {"final_response": ""}
            return

        request = ConversationRequest(
            conversation_id=str(conversation.id),
            prompt=self._to_prompt_string(input),
            cwd=str(conversation.project.path),
            profile={"model": model} if model else {},
            # Resume only if THIS coworker produced a prior turn here — see
            # should_resume(). Chat history is a UI artifact; each coworker
            # keeps its own native session (switch semantics decided
            # 2026-07-03).
            resume=self.should_resume(conversation.messages),
        )

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[NormalizedEvent | None] = asyncio.Queue()

        def _put(event: NormalizedEvent) -> None:
            loop.call_soon_threadsafe(queue.put_nowait, event)

        def run() -> None:
            try:
                self._run_cli(cli=cli, request=request, resume=request.resume and self.config.supports_resume,
                               emit=_put, allow_retry=True)
            finally:
                loop.call_soon_threadsafe(queue.put_nowait, None)

        task = loop.run_in_executor(None, run)

        final_text = ""
        while True:
            event = await queue.get()
            if event is None:
                break
            if event.type == "completed":
                final_text = event.final_text or ""
                continue
            yield self._legacy_dict(event)

        await task
        yield {"final_response": final_text}

    @staticmethod
    def _to_prompt_string(input_blocks: list[dict]) -> str:
        parts = []
        for block in input_blocks:
            if block.get("type") == "text":
                parts.append(block["text"])
            elif block.get("type") == "file":
                parts.append(f"[Attached file '{block['filename']}': {block['path']}]")
            elif block.get("type") == "image":
                parts.append("[Attached image — pass a file path instead]")
        return "\n\n".join(parts)

    @staticmethod
    def _legacy_dict(event: NormalizedEvent) -> dict:
        """Adapts a NormalizedEvent to the dict shape format_hermes_stream
        expects, so CLI harnesses reuse the existing SSE formatter without
        it needing to know about the normalized event model."""
        if event.type == "text_chunk":
            return {"type": "delta", "delta": event.text or ""}
        if event.type == "tool_call":
            return {"type": "thought.tool_call.start", "tool_call_id": event.tool_call_id or "",
                    "name": event.tool_name or "tool", "args": event.tool_args}
        if event.type == "tool_result":
            return {"type": "thought.tool_call.end", "tool_call_id": event.tool_call_id or "",
                    "result": event.tool_result or ""}
        if event.type in ("thinking_chunk", "progress"):
            return {"type": "thought.progress", "content": event.text or event.detail or ""}
        if event.type == "error":
            return {"type": "delta", "delta": f"\n\n[{event.detail or 'error'}]"}
        return {"type": "delta", "delta": ""}

    def _run_cli(self, *, cli: str, request: ConversationRequest, resume: bool, emit, allow_retry: bool) -> None:
        """Blocking subprocess runner (executor thread)."""
        env = dict(os.environ)
        for key in self.env_removals():
            env.pop(key, None)
        env.update(self.env_overrides())
        # Same PATH the CLI was discovered with — a CLI that find_cli()
        # located via search_path()'s appended dirs must also be able to
        # re-resolve itself/its helpers when it spawns children.
        env["PATH"] = self.search_path()

        cwd = Path(request.cwd)
        cwd.mkdir(parents=True, exist_ok=True)
        # Per-project persistent memory: CLAUDE.md in the project folder is
        # auto-read by Claude Code (and compatible CLIs) every turn. Ensure
        # it exists here too — projects created before the scaffold landed
        # (e.g. 'general') get it lazily on their next turn. Idempotent.
        from cowork.services.projects import ProjectService
        try:
            ProjectService.scaffold_memory(cwd, cwd.name)
        except OSError as exc:
            logger.warning("could not scaffold project memory in %s: %s", cwd, exc)

        args = [*self.spawn_argv(cli), *self.build_arguments(request, resume=resume)]
        proc = subprocess.Popen(
            args, cwd=request.cwd, env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )

        got_any_output = False
        final_text = ""
        stderr_tail = ""
        # Generic fallback for CLIs with no structured completion marker
        # (a plain-text CLI like Antigravity never emits a `completed`
        # event — every line is just a text_chunk). If the subclass never
        # sets final_text explicitly, it's assembled from the streamed
        # text_chunks instead. A no-op for CLIs (like Claude) whose
        # parse_line does emit an explicit completed/final_text.
        accumulated_text: list[str] = []
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                event = self.parse_line(line)
                if event is None:
                    continue
                got_any_output = True
                if event.type == "completed":
                    final_text = event.final_text or ""
                    continue
                if event.type == "text_chunk" and event.text:
                    accumulated_text.append(event.text)
                emit(event)
            proc.wait(timeout=TURN_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            emit(NormalizedEvent(type="error", detail=f"{self.label} turn timed out after {TURN_TIMEOUT_SECONDS}s and was terminated."))
            emit(NormalizedEvent(type="completed", final_text=""))
            return
        finally:
            # Never leak the child: a parse_line bug or generator teardown
            # mid-stream must not leave a headless CLI running (leaked
            # children accumulate and eventually make ALL spawns flaky —
            # 0xC0000142-style init failures under resource pressure).
            # kill() alone doesn't reap it — wait() is required or the
            # process lingers as a zombie until this Popen object is GC'd.
            if proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            try:
                if proc.stderr is not None:
                    stderr_tail = proc.stderr.read()[-2000:]
            except Exception:
                pass

        # 0xC0000142 (STATUS_DLL_INIT_FAILED): Windows refused to even
        # initialize the process — transient resource pressure, not a CLI
        # or login problem. Nothing executed, so one retry is safe.
        WIN_INIT_FAILED = 3221225794

        if proc.returncode not in (0, None) and not got_any_output:
            # A resume against a session the CLI doesn't know (e.g. its
            # state was cleared) fails fast with no stream output — retry
            # once as a fresh session with the same id.
            if resume and allow_retry:
                logger.warning("%s --resume failed (rc=%s); retrying as a new session. stderr: %s",
                                self.label, proc.returncode, stderr_tail)
                self._run_cli(cli=cli, request=request, resume=False, emit=emit, allow_retry=False)
                return
            if proc.returncode == WIN_INIT_FAILED and allow_retry:
                logger.warning("%s failed to initialize (0xC0000142); retrying once.", self.label)
                self._run_cli(cli=cli, request=request, resume=resume, emit=emit, allow_retry=False)
                return
            if proc.returncode == WIN_INIT_FAILED:
                emit(NormalizedEvent(type="error", detail=(
                    f"{self.label} could not start (Windows process-init failure 0xC0000142). "
                    "This is a transient system condition, not a login issue — try sending again; "
                    "if it persists, close unused apps or restart the machine."
                )))
                emit(NormalizedEvent(type="completed", final_text=""))
                return
            emit(NormalizedEvent(type="error", detail=(
                f"{self.label} exited with code {proc.returncode}. "
                f"{('Details: ' + stderr_tail) if stderr_tail else f'Run `{self.config.executable}` in a terminal to check login status.'}"
            )))
            emit(NormalizedEvent(type="completed", final_text=""))
            return

        emit(NormalizedEvent(type="completed", final_text=final_text or "\n".join(accumulated_text)))
