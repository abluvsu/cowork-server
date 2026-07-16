"""Registry + lifecycle supervisor for local daemon processes the app leans
on (OmniRoute today; the WhatsApp bridge is next — see docs/plans/PLAN_A_FABLE.md
A1). `frontend/scripts/start-omniroute.mjs` covers the dev-web fast path; this
module is the supervisor the Electron app and the backend itself can share, so
neither run path is missing it.

Windows process-tree lesson (mirrors frontend/scripts/start-server.mjs):
killing a cmd.exe wrapper alone leaves its child running as an orphan holding
the port — only a tree-kill (`taskkill /T /F`) actually ends it. We spawn the
real binary directly where possible; where a wrapper is unavoidable (an npm
`.cmd` shim on Windows) we resolve the LISTENING pid via the port instead of
trusting the wrapper's own pid, and tree-kill that.
"""
from __future__ import annotations

import logging
import os
import platform
import re
import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# Only ever reap a process that looks like one of ours — never an unrelated
# app that happens to be sitting on the port.
_KILLABLE_IMAGE = re.compile(r"omniroute|node|python|cowork|uvicorn|uv(\.exe)?$", re.IGNORECASE)


@dataclass
class ServiceDefinition:
    id: str
    label: str
    health_url: str
    find_binary: "callable[[], str | None]"
    spawn_args: "callable[[str], list[str]]"
    install_hint: str
    autostart: bool = True


@dataclass
class ServiceStatus:
    id: str
    label: str
    status: str = "unknown"  # running | starting | dead
    detail: str = ""
    pid: int | None = None
    adopted: bool = False


def _probe(url: str, timeout: float = 1.5) -> bool:
    try:
        resp = httpx.get(url, timeout=timeout)
        return resp.status_code == 200
    except httpx.HTTPError:
        return False


def _port_of(url: str) -> int | None:
    parsed = urlparse(url)
    return parsed.port


def _run_text(cmd: list[str], timeout: float = 5.0) -> str:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                                 creationflags=subprocess.CREATE_NO_WINDOW if platform.system() == "Windows" else 0)
        return result.stdout or ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _find_listening_pid(port: int) -> tuple[int, str] | None:
    """Returns (pid, image_name) of whatever holds `port` LISTENING, or None."""
    if platform.system() == "Windows":
        netstat = _run_text(["netstat", "-ano", "-p", "TCP"])
        pid = None
        for line in netstat.splitlines():
            m = re.match(r"\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)", line, re.IGNORECASE)
            if m and int(m.group(1)) == port:
                pid = int(m.group(2))
                break
        if pid is None:
            return None
        csv = _run_text(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"])
        m = re.match(r'^"([^"]+)"', csv)
        return pid, (m.group(1) if m else "")
    else:
        out = _run_text(["lsof", "-ti", f"tcp:{port}", "-sTCP:LISTEN"])
        pids = [int(p) for p in out.split() if p.strip().isdigit()]
        if not pids:
            return None
        return pids[0], ""


def _kill_tree(pid: int) -> None:
    if platform.system() == "Windows":
        _run_text(["taskkill", "/PID", str(pid), "/T", "/F"])
    else:
        try:
            os.killpg(pid, 15)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                os.kill(pid, 15)
            except (ProcessLookupError, PermissionError, OSError):
                pass


class LocalServiceRegistry:
    """Process-wide singleton; see get_registry()."""

    def __init__(self) -> None:
        self._definitions: dict[str, ServiceDefinition] = {}
        self._processes: dict[str, subprocess.Popen] = {}
        self._adopted: set[str] = set()
        self._lock = threading.Lock()

    def register(self, definition: ServiceDefinition) -> None:
        with self._lock:
            self._definitions[definition.id] = definition

    def list_ids(self) -> list[str]:
        return list(self._definitions.keys())

    def status(self, service_id: str) -> ServiceStatus:
        definition = self._definitions.get(service_id)
        if definition is None:
            raise KeyError(service_id)
        healthy = _probe(definition.health_url)
        proc = self._processes.get(service_id)
        if healthy:
            pid = proc.pid if proc is not None else None
            return ServiceStatus(id=service_id, label=definition.label, status="running",
                                  pid=pid, adopted=service_id in self._adopted)
        if proc is not None and proc.poll() is None:
            return ServiceStatus(id=service_id, label=definition.label, status="starting", pid=proc.pid)
        return ServiceStatus(id=service_id, label=definition.label, status="dead",
                              detail=f"not reachable at {definition.health_url}")

    def list_status(self) -> list[ServiceStatus]:
        return [self.status(sid) for sid in self._definitions]

    def ensure_started(self, service_id: str) -> ServiceStatus:
        """Adopt-if-healthy, else spawn detached. Never raises — a local
        service must never take the app down with it; failures surface as
        status='dead' with a `detail` message."""
        definition = self._definitions.get(service_id)
        if definition is None:
            raise KeyError(service_id)

        if _probe(definition.health_url):
            self._adopted.add(service_id)
            logger.info("[local-services] %s already running — adopting", service_id)
            return self.status(service_id)

        binary = definition.find_binary()
        if not binary:
            logger.warning("[local-services] %s not installed (%s)", service_id, definition.install_hint)
            return ServiceStatus(id=service_id, label=definition.label, status="dead",
                                  detail=definition.install_hint)

        args = definition.spawn_args(binary)
        try:
            proc = subprocess.Popen(
                args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=(platform.system() != "Windows"),
                creationflags=(subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
                if platform.system() == "Windows" else 0,
            )
        except OSError as exc:
            logger.warning("[local-services] failed to spawn %s: %s", service_id, exc)
            return ServiceStatus(id=service_id, label=definition.label, status="dead", detail=str(exc))

        with self._lock:
            self._processes[service_id] = proc
            self._adopted.discard(service_id)
        logger.info("[local-services] %s starting (pid %d)", service_id, proc.pid)
        return ServiceStatus(id=service_id, label=definition.label, status="starting", pid=proc.pid)

    def restart(self, service_id: str) -> ServiceStatus:
        definition = self._definitions.get(service_id)
        if definition is None:
            raise KeyError(service_id)

        proc = self._processes.get(service_id)
        if proc is not None:
            _kill_tree(proc.pid)
            self._processes.pop(service_id, None)
        else:
            # We never spawned it ourselves (adopted or fully external) —
            # reap by port, image-name-checked so we never kill something
            # that isn't ours.
            port = _port_of(definition.health_url)
            if port is not None:
                found = _find_listening_pid(port)
                if found is not None:
                    pid, image = found
                    if _KILLABLE_IMAGE.search(image or ""):
                        _kill_tree(pid)
                    else:
                        logger.warning("[local-services] pid %d (%s) owns %s's port but isn't ours — leaving it alone",
                                       pid, image, service_id)
        self._adopted.discard(service_id)
        return self.ensure_started(service_id)


_registry: LocalServiceRegistry | None = None
_registry_lock = threading.Lock()


def _find_omniroute_binary() -> str | None:
    if platform.system() == "Windows":
        candidate = Path(os.environ.get("APPDATA", "")) / "npm" / "omniroute.cmd"
        return str(candidate) if candidate.exists() else None
    for candidate in (Path.home() / ".local" / "bin" / "omniroute", Path("/usr/local/bin/omniroute")):
        if candidate.exists():
            return str(candidate)
    return None


def _omniroute_spawn_args(binary: str) -> list[str]:
    # Pin the port and suppress the dashboard browser-popup. A bare
    # `omniroute` walks to other ports when 20128 is contested — observed
    # 2026-07-16: a double-spawn race left an instance on :20131 whose web
    # dashboard squatted vite's :5173. Pinning makes the loser of a spawn
    # race fail instead of walking.
    serve_args = ["serve", "--port", "20128", "--no-open"]
    if platform.system() == "Windows":
        return [os.environ.get("COMSPEC", "cmd.exe"), "/c", binary, *serve_args]
    return [binary, *serve_args]


def get_registry() -> LocalServiceRegistry:
    global _registry
    with _registry_lock:
        if _registry is None:
            _registry = LocalServiceRegistry()
            _registry.register(ServiceDefinition(
                id="omniroute",
                label="OmniRoute (free-model gateway)",
                health_url="http://127.0.0.1:20128/v1/models",
                find_binary=_find_omniroute_binary,
                spawn_args=_omniroute_spawn_args,
                install_hint="not installed — run `npm install -g omniroute`",
                autostart=True,
            ))
        return _registry
