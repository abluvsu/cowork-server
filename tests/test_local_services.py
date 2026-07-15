"""Local-services supervisor (PLAN_A_FABLE A1): registry CRUD, the
adopt-vs-spawn decision, and the image-name-checked reaper.

All process/network boundaries are mocked — these tests never touch a real
port or spawn a real process.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from cowork.services.local_services import (
    LocalServiceRegistry,
    ServiceDefinition,
    _find_listening_pid,
    _kill_tree,
)


def _definition(**overrides) -> ServiceDefinition:
    base = dict(
        id="thing",
        label="Thing",
        health_url="http://127.0.0.1:9999/v1/models",
        find_binary=lambda: "/usr/local/bin/thing",
        spawn_args=lambda binary: [binary],
        install_hint="not installed",
    )
    base.update(overrides)
    return ServiceDefinition(**base)


def test_register_and_list_ids():
    registry = LocalServiceRegistry()
    registry.register(_definition())
    assert registry.list_ids() == ["thing"]


def test_status_unknown_service_raises_keyerror():
    registry = LocalServiceRegistry()
    with pytest.raises(KeyError):
        registry.status("nope")


@patch("cowork.services.local_services._probe", return_value=True)
def test_ensure_started_adopts_already_healthy_service(_mock_probe):
    registry = LocalServiceRegistry()
    registry.register(_definition())
    spawn = MagicMock()
    with patch("subprocess.Popen", spawn):
        result = registry.ensure_started("thing")
    spawn.assert_not_called()
    assert result.status == "running"
    assert result.adopted is True


@patch("cowork.services.local_services._probe", return_value=False)
def test_ensure_started_spawns_when_binary_available(_mock_probe):
    registry = LocalServiceRegistry()
    registry.register(_definition())
    fake_proc = MagicMock(pid=4242)
    with patch("subprocess.Popen", return_value=fake_proc) as spawn:
        result = registry.ensure_started("thing")
    spawn.assert_called_once()
    assert result.status == "starting"
    assert result.pid == 4242


@patch("cowork.services.local_services._probe", return_value=False)
def test_ensure_started_reports_dead_when_binary_missing(_mock_probe):
    registry = LocalServiceRegistry()
    registry.register(_definition(find_binary=lambda: None))
    with patch("subprocess.Popen") as spawn:
        result = registry.ensure_started("thing")
    spawn.assert_not_called()
    assert result.status == "dead"
    assert result.detail == "not installed"


def test_restart_kills_tree_of_our_own_process_then_respawns():
    registry = LocalServiceRegistry()
    registry.register(_definition())
    fake_proc = MagicMock(pid=111)
    fake_proc.poll.return_value = None
    with patch("cowork.services.local_services._probe", return_value=False), \
         patch("subprocess.Popen", return_value=fake_proc):
        registry.ensure_started("thing")

    with patch("cowork.services.local_services._probe", return_value=True), \
         patch("cowork.services.local_services._kill_tree") as kill_tree:
        result = registry.restart("thing")

    kill_tree.assert_called_once_with(111)
    assert result.status == "running"


def test_reaper_never_kills_an_unrelated_process_on_the_port():
    """A process that owns the port but isn't ours (a name mismatch) must
    never be tree-killed — mirrors the image-name check in
    frontend/scripts/start-server.mjs's reapPort()."""
    registry = LocalServiceRegistry()
    registry.register(_definition())
    with (
        patch("cowork.services.local_services._probe", return_value=False),
        patch("cowork.services.local_services._find_listening_pid", return_value=(555, "Discord.exe")),
        patch("cowork.services.local_services._kill_tree") as kill_tree,
        patch("subprocess.Popen", return_value=MagicMock(pid=999)),
    ):
        registry.restart("thing")
    kill_tree.assert_not_called()


def test_reaper_kills_a_recognized_orphan_image_on_the_port():
    registry = LocalServiceRegistry()
    registry.register(_definition())
    with (
        patch("cowork.services.local_services._probe", return_value=False),
        patch("cowork.services.local_services._find_listening_pid", return_value=(555, "omniroute.exe")),
        patch("cowork.services.local_services._kill_tree") as kill_tree,
        patch("subprocess.Popen", return_value=MagicMock(pid=999)),
    ):
        registry.restart("thing")
    kill_tree.assert_called_once_with(555)


def test_find_listening_pid_returns_none_on_probe_failure():
    with patch("cowork.services.local_services._run_text", return_value=""):
        assert _find_listening_pid(9999) is None


def test_kill_tree_swallows_errors_for_nonexistent_pid():
    # Must never raise even if the pid is already gone.
    _kill_tree(999999999)
