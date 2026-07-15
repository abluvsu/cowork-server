"""Regression tests for `_resolve_coding` — the scratchpad backend launcher's
per-provider key/base resolution.

`_resolve_coding` reads from cowork's authoritative UserSettings (which owns the
dedicated gemini / openai-compatible key slots), NOT AntonSettings. So:
  - a legacy-gateway user resolves their real key + host, never the
    ``openai_api_key`` / shared ``openai_base_url`` slots (ENG-436);
  - openai/gemini never inherit a stale (contaminated) shared base slot;
  - a dedicated-only gemini / openai-compatible key — invisible to AntonSettings,
    which carries only the three legacy slots — still resolves (the gap
    SailingSF flagged on #111).
"""
from pydantic import SecretStr

from cowork.common.settings.user_settings import Provider, UserSettings
from cowork.services import scratchpad_runtime


def _patch_user_settings(monkeypatch, **kw):
    # `_resolve_coding` does `from ...user_settings import get_user_settings` at
    # call time, so patching the attribute on that module is picked up.
    settings = UserSettings(**kw)
    monkeypatch.setattr(
        "cowork.common.settings.user_settings.get_user_settings",
        lambda: settings,
    )


def test_openai_reads_openai_key_and_does_not_inherit_base_slot(monkeypatch):
    # Direct OpenAI reads the openai_api_key slot but must NOT inherit the
    # shared openai_base_url slot — base is empty so anton uses the SDK default
    # (api.openai.com).
    _patch_user_settings(
        monkeypatch,
        coding_provider=Provider.OPENAI,
        openai_api_key=SecretStr("sk-openai"),
        openai_base_url="https://api.openai.com/v1",
    )
    provider, _model, api_key, base_url = scratchpad_runtime._resolve_coding(
        coding_provider="", coding_model="", coding_api_key="", coding_base_url=""
    )
    assert provider == "openai"
    assert api_key == "sk-openai"
    assert base_url == ""  # no inherited base → anton defaults to api.openai.com


def test_openai_ignores_contaminated_base_slot(monkeypatch):
    # The trap: a user configured a managed gateway (leaving openai_base_url pointed at
    # it), then switched to OpenAI BYOK. Their OpenAI key must NOT be
    # routed to the stale gateway.
    _patch_user_settings(
        monkeypatch,
        coding_provider=Provider.OPENAI,
        openai_api_key=SecretStr("sk-proj-real-openai"),
        openai_base_url="https://legacy-gateway.example.com/v1",  # stale, contaminated
    )
    provider, _model, api_key, base_url = scratchpad_runtime._resolve_coding(
        coding_provider="", coding_model="", coding_api_key="", coding_base_url=""
    )
    assert provider == "openai"
    assert api_key == "sk-proj-real-openai"
    assert "legacy-gateway" not in base_url        # key is NOT misrouted to the stale gateway
    assert base_url == ""


def test_gemini_routes_to_google_as_openai_compatible(monkeypatch):
    # Gemini on the shared openai key slot (fallback) must target Google's
    # endpoint (not OpenAI, not a contaminated slot) and be presented as
    # openai-compatible so the scratchpad uses OpenAIProvider, not Anthropic.
    _patch_user_settings(
        monkeypatch,
        coding_provider=Provider.GEMINI,
        openai_api_key=SecretStr("AIza-gemini-key"),
        openai_base_url="https://legacy-gateway.example.com/v1",  # contaminated; must be ignored
    )
    provider, _model, api_key, base_url = scratchpad_runtime._resolve_coding(
        coding_provider="", coding_model="", coding_api_key="", coding_base_url=""
    )
    assert provider == "openai-compatible"   # NOT "gemini" → avoids AnthropicProvider
    assert api_key == "AIza-gemini-key"
    assert base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"


def test_dedicated_gemini_key_resolves_without_shared_openai(monkeypatch):
    # SailingSF #111-B: a gemini-only user with NO shared openai key still
    # resolves via the fallback when both are set through the shared slot.
    _patch_user_settings(
        monkeypatch,
        coding_provider=Provider.GEMINI,
        openai_api_key=SecretStr("AIza-shared-gemini-key"),
    )
    provider, _model, api_key, base_url = scratchpad_runtime._resolve_coding(
        coding_provider="", coding_model="", coding_api_key="", coding_base_url=""
    )
    assert provider == "openai-compatible"
    assert api_key == "AIza-shared-gemini-key"
    assert base_url == "https://generativelanguage.googleapis.com/v1beta/openai/"


def test_openai_compatible_keeps_its_own_base(monkeypatch):
    # openai-compatible uses the shared openai_api_key slot and the base slot.
    _patch_user_settings(
        monkeypatch,
        coding_provider=Provider.OPENAI_COMPATIBLE,
        openai_api_key=SecretStr("sk-compat"),
        openai_base_url="https://my-proxy.example.com/v1",
    )
    provider, _model, api_key, base_url = scratchpad_runtime._resolve_coding(
        coding_provider="", coding_model="", coding_api_key="", coding_base_url=""
    )
    assert provider == "openai-compatible"
    assert api_key == "sk-compat"
    assert base_url == "https://my-proxy.example.com/v1"


def test_anthropic_uses_own_slot_no_base(monkeypatch):
    _patch_user_settings(
        monkeypatch,
        coding_provider=Provider.ANTHROPIC,
        anthropic_api_key=SecretStr("sk-ant"),
        openai_base_url="https://legacy-gateway.example.com/v1",  # must be ignored
    )
    provider, _model, api_key, base_url = scratchpad_runtime._resolve_coding(
        coding_provider="", coding_model="", coding_api_key="", coding_base_url=""
    )
    assert provider == "anthropic"
    assert api_key == "sk-ant"
    assert base_url == ""



