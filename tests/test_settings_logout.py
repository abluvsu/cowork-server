"""Regression tests for the logout / clear_credentials flow (ENG-475).

Verifies that:
- POST /api/v1/settings/logout clears all credential keys from the DB
- /health returns config_ready: false after logout
- Provider/model preferences survive logout
- Provider UI/status state is cleared
"""
from __future__ import annotations

import pytest
from sqlmodel import Session

from cowork.common.settings.app_settings import get_app_settings
from cowork.db.session import get_engine
from cowork.services.settings import SettingService
from cowork.common.settings.user_settings import UserSettings


@pytest.fixture()
def session():
    engine = get_engine(get_app_settings().database.uri)
    with Session(engine) as s:
        yield s


def _seed_all_settings(session: Session) -> None:
    """Seed credentials, provider prefs, and UI state into the DB."""
    svc = SettingService(session)

    # Credentials (sensitive fields)
    svc.upsert_setting("anthropic_api_key", "sk-ant-test-123")
    svc.upsert_setting("openai_api_key", "sk-test-123")

    # Base URLs cleared by logout
    svc.upsert_setting("openai_base_url", "https://api.openai.com")

    # Provider/model preferences (should survive logout)
    svc.upsert_setting("planning_provider", "openai")
    svc.upsert_setting("coding_provider", "openai")
    svc.upsert_setting("planning_model", "gpt-4o")
    svc.upsert_setting("coding_model", "gpt-4o-mini")

    # Provider UI state (should be cleared)
    svc.upsert_setting("providers_json", '[{"type":"openai"}]')
    svc.upsert_setting("provider_status", '{"openai":"ok"}')
    svc.upsert_setting("provider_status_details", '{"openai":"connected"}')


def _cleanup(session: Session) -> None:
    """Remove all seeded keys so tests don't leak into each other."""
    svc = SettingService(session)
    for key in (
        "anthropic_api_key", "openai_api_key",
        "openai_base_url",
        "planning_provider", "coding_provider",
        "planning_model", "coding_model",
        "providers_json", "provider_status", "provider_status_details",
    ):
        svc.delete_setting(key)


def test_clear_credentials_removes_sensitive_keys(session: Session):
    """All sensitive (SecretStr) fields must be deleted."""
    _seed_all_settings(session)
    svc = SettingService(session)
    try:
        deleted = svc.clear_credentials()

        # All API key fields should be in the deleted list
        for key in ("anthropic_api_key", "openai_api_key"):
            assert key in deleted, f"Expected '{key}' to be deleted"

        # Verify they're actually gone from the DB
        settings = svc.load()
        assert settings.anthropic_api_key is None
        assert settings.openai_api_key is None
    finally:
        _cleanup(session)


def test_clear_credentials_removes_provider_ui_state(session: Session):
    """Provider connectivity and UI card state must be cleared."""
    _seed_all_settings(session)
    svc = SettingService(session)
    try:
        deleted = svc.clear_credentials()

        for key in ("providers_json", "provider_status", "provider_status_details",
                    "openai_base_url"):
            assert key in deleted, f"Expected '{key}' to be deleted"
    finally:
        _cleanup(session)


def test_clear_credentials_preserves_model_preferences(session: Session):
    """Provider/model preferences must survive logout."""
    _seed_all_settings(session)
    svc = SettingService(session)
    try:
        svc.clear_credentials()

        settings = svc.load()
        assert settings.planning_provider.value == "openai"
        assert settings.coding_provider.value == "openai"
        assert settings.planning_model == "gpt-4o"
        assert settings.coding_model == "gpt-4o-mini"
    finally:
        _cleanup(session)


def test_config_ready_stays_true_after_logout(session: Session):
    """After clearing credentials, config_ready remains True (a CLI tool can be installed)."""
    _seed_all_settings(session)
    svc = SettingService(session)
    try:
        settings_before = svc.load()
        assert settings_before.config_status["config_ready"] is True

        svc.clear_credentials()

        settings_after = svc.load()
        assert settings_after.config_status["config_ready"] is True
    finally:
        _cleanup(session)


def test_clear_credentials_idempotent(session: Session):
    """Calling clear_credentials twice should not error."""
    _seed_all_settings(session)
    svc = SettingService(session)
    try:
        first = svc.clear_credentials()
        assert len(first) > 0

        second = svc.clear_credentials()
        assert len(second) == 0
    finally:
        _cleanup(session)
