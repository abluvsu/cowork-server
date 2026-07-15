"""User-facing turn-error handling (ported from cowork PR #156).

When a turn dies on a cryptic provider 400 — most notably an image
arriving as an OpenAI-style ``image_url`` block instead of Anthropic's
``image`` block — the handler must surface a clean ``response.failed``
event (streaming) / 400 (non-streaming) with curated copy, while any
unmapped failure stays generic so provider internals never leak.

These tests pin the detection/mapping policy and the handler emission on
both the streaming and non-streaming paths.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from fastapi import HTTPException

from cowork.handlers import turn_errors as te
from cowork.handlers.responses import ResponsesHandler


# ── Detection / mapping policy ────────────────────────────────────

def test_detects_anthropic_image_url_rejection():
    exc = Exception(
        "Input tag 'image_url' found using 'type' does not match "
        "any of the expected tags: 'image'"
    )
    assert te.is_image_format_error(exc) is True


def test_detects_unsupported_image_phrasing():
    assert te.is_image_format_error(Exception("Unsupported image media type")) is True


def test_ignores_unrelated_errors():
    assert te.is_image_format_error(Exception("Internal server error")) is False
    # A tool_use 400 must NOT be misread as an image failure.
    assert te.is_image_format_error(
        Exception("tool_use ids were found without tool_result blocks")
    ) is False


def test_maps_image_error_to_curated_copy():
    result = te.friendly_turn_error(
        Exception("'image_url' does not match expected tags: 'image'")
    )
    assert result is not None
    code, message = result
    assert code == "image_format"
    assert "PNG or JPEG" in message


def test_returns_none_for_unmapped_error():
    assert te.friendly_turn_error(Exception("boom")) is None


def test_response_failed_sse_shape():
    frame = te.response_failed_sse("oops", "image_format")
    assert frame.startswith("event: response.failed\ndata: ")
    assert frame.endswith("\n\n")
    payload = json.loads(frame.split("data: ", 1)[1].strip())
    assert payload == {"type": "response.failed", "code": "image_format", "error": "oops"}


# ── Handler emission (non-streaming / _collect path) ─────────────

def _handler_with_raising_formatter(exc: Exception) -> ResponsesHandler:
    from unittest.mock import MagicMock
    from sqlmodel import Session

    from cowork.db.session import get_engine
    from cowork.common.settings.app_settings import get_app_settings

    engine = get_engine(get_app_settings().database.uri)
    handler = ResponsesHandler.__new__(ResponsesHandler)
    handler.session = Session(engine)

    async def _formatter(stream, model, event_sink):
        yield "event: response.created\ndata: {}\n\n"
        raise exc

    async def _stream_response(*, conversation, input, model=None, disabled_connections=None):
        if False:
            yield

    class _Harness:
        id = "anton"
        formatter = staticmethod(_formatter)
        stream_response = staticmethod(_stream_response)

    handler.harness = _Harness()
    return handler


def test_collect_raises_400_with_curated_message_for_image_error():
    handler = _handler_with_raising_formatter(
        Exception("'image_url' does not match the expected tags: 'image'")
    )
    with pytest.raises(HTTPException) as err:
        asyncio.run(handler._collect(stream=None, conversation_id=uuid4(), model="anton", output_item_id="msg-1"))
    assert err.value.status_code == 400
    assert "PNG or JPEG" in err.value.detail


def test_collect_raises_500_generic_for_unmapped_error():
    handler = _handler_with_raising_formatter(Exception("kaboom: secret-token-xyz"))
    with pytest.raises(HTTPException) as err:
        asyncio.run(handler._collect(stream=None, conversation_id=uuid4(), model="anton", output_item_id="msg-1"))
    assert err.value.status_code == 500
    assert err.value.detail == te.GENERIC_TURN_ERROR_MESSAGE
    assert "secret-token" not in err.value.detail


# ── Token-limit (quota) detection / mapping ───────────────────────
#
# When an account's included-token allowance is spent, anton raises
# TokenLimitExceeded mid-turn. Before this was mapped, the exception
# aborted the SSE generator with no terminal event — the connection just
# closed and the renderer's spinner stopped, reading as "Anton is dead".
# These tests pin that a quota failure now surfaces curated copy on both
# paths instead.

# The stable 429 message anton builds for this case. Used to exercise the
# type-independent fallback path (no anton import needed).
_TOKEN_LIMIT_MESSAGE = (
    "Server returned 429 — Monthly limit exceeded for tokens: 5000000/5000000 "
    "Visit https://console.example.com to upgrade or to top up your tokens."
)


def test_detects_token_limit_via_anton_type():
    provider = pytest.importorskip("anton.core.llm.provider")
    assert te.is_token_limit_error(provider.TokenLimitExceeded(_TOKEN_LIMIT_MESSAGE)) is True


def test_detects_token_limit_via_message_fallback():
    # Even when the anton type isn't importable, the 429 message is stable
    # enough to recognise so the quota case never falls through to generic.
    assert te.is_token_limit_error(Exception(_TOKEN_LIMIT_MESSAGE)) is True


def test_token_limit_requires_both_signals():
    # A bare 429 (rate limit, not quota) or the tokens phrase on its own
    # must NOT be misread as an exhausted allowance.
    assert te.is_token_limit_error(Exception("Server returned 429 — too many requests")) is False
    assert te.is_token_limit_error(Exception("Monthly limit exceeded for tokens")) is False


def test_maps_token_limit_to_curated_copy():
    result = te.friendly_turn_error(Exception(_TOKEN_LIMIT_MESSAGE))
    assert result is not None
    code, message = result
    assert code == te.TOKEN_LIMIT_CODE
    assert message == te.TOKEN_LIMIT_USER_MESSAGE
    # Raw provider usage figures must not leak into the user copy.
    assert "5000000" not in message


def test_token_limit_takes_precedence_over_generic():
    # A quota failure must map to curated copy, never the redacted generic.
    code, _ = te.friendly_turn_error(Exception(_TOKEN_LIMIT_MESSAGE))
    assert code != te.GENERIC_TURN_ERROR_CODE


def test_collect_raises_400_with_curated_message_for_token_limit():
    handler = _handler_with_raising_formatter(Exception(_TOKEN_LIMIT_MESSAGE))
    with pytest.raises(HTTPException) as err:
        asyncio.run(handler._collect(stream=None, conversation_id=uuid4(), model="anton", output_item_id="msg-1"))
    assert err.value.status_code == 400
    assert err.value.detail == te.TOKEN_LIMIT_USER_MESSAGE


# ── Provider auth (401) → provider_auth ──────────────────────────────


def test_detects_auth_error_from_openai_401_message():
    # anton's openai provider maps a gateway 401 to this ConnectionError message.
    exc = ConnectionError("Invalid API key — check your OpenAI API key configuration.")
    assert te.is_auth_error(exc) is True


def test_detects_auth_error_from_anthropic_401_message():
    exc = ConnectionError("Invalid API key — check your ANTHROPIC_API_KEY environment variable.")
    assert te.is_auth_error(exc) is True


def test_bare_401_not_flagged():
    # Tightened: a 401/"unauthorized" without anton's specific "Invalid API key"
    # copy is NOT a provider-auth error (avoids mislabeling e.g. a tool API 401).
    assert te.is_auth_error(Exception("Server returned 401 — Unauthorized")) is False
    assert te.is_auth_error(Exception("connection reset")) is False


def test_auth_error_maps_to_provider_auth_code():
    code, message = te.friendly_turn_error(
        ConnectionError("Invalid API key — check your OpenAI API key configuration.")
    )
    assert code == te.AUTH_ERROR_CODE == "provider_auth"
    assert "settings" in message.lower()


def test_token_limit_wins_over_auth_for_credit_case():
    # A 429 credit/quota case must stay token_limit, not be misread as auth.
    code, _ = te.friendly_turn_error(Exception(_TOKEN_LIMIT_MESSAGE))
    assert code == te.TOKEN_LIMIT_CODE


def test_auth_error_detail_is_provider_aware():
    # Managed gateway → reconnect; BYOK → fix your own key in Settings (no "reconnect").
    managed = te.auth_error_detail("Gateway", reconnectable=True)
    assert "reconnect" in managed.lower()
    byok = te.auth_error_detail("OpenAI", reconnectable=False)
    assert "reconnect" not in byok.lower()
    assert "OpenAI" in byok and "Settings" in byok


def test_response_failed_payload_carries_auth_fields():
    p = te.response_failed_payload("msg", te.AUTH_ERROR_CODE, reconnectable=True, provider_label="Gateway")
    assert p["reconnectable"] is True and p["provider_label"] == "Gateway"
    # Unrelated failures keep the original shape (no extra keys).
    assert "reconnectable" not in te.response_failed_payload("boom", "anton_error")
