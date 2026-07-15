"""Tests for provider_api_key — per-provider key resolution with fallback.

gemini falls back to the shared openai_api_key when no dedicated slot is set,
so existing single-key configs keep working with no migration.
"""

from pydantic import SecretStr

from cowork.common.settings.user_settings import (
    Provider,
    UserSettings,
    provider_api_key,
)


def _settings(**kw):
    return UserSettings(**kw)


def _val(secret):
    return secret.get_secret_value() if secret else None


class TestProviderApiKey:
    def test_openai_reads_own_slot(self):
        s = _settings(openai_api_key=SecretStr("sk-openai"))
        assert _val(provider_api_key(s, Provider.OPENAI)) == "sk-openai"

    def test_anthropic_reads_own_slot(self):
        s = _settings(anthropic_api_key=SecretStr("sk-ant"))
        assert _val(provider_api_key(s, Provider.ANTHROPIC)) == "sk-ant"

    def test_gemini_falls_back_to_openai_when_unset(self):
        s = _settings(openai_api_key=SecretStr("sk-shared"))
        assert _val(provider_api_key(s, Provider.GEMINI)) == "sk-shared"

    def test_openai_compatible_falls_back_to_openai_when_unset(self):
        s = _settings(openai_api_key=SecretStr("sk-shared"))
        assert _val(provider_api_key(s, Provider.OPENAI_COMPATIBLE)) == "sk-shared"

    def test_all_unset_returns_none(self):
        assert provider_api_key(_settings(), Provider.GEMINI) is None


class TestConfigStatusFallback:
    def test_gemini_planning_on_shared_key_reads_as_configured(self):
        s = _settings(
            planning_provider=Provider.GEMINI,
            openai_api_key=SecretStr("sk-shared"),
        )
        assert s.config_status["config_ready"] is True
