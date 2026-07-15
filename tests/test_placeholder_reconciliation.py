"""A turn killed mid-stream (process crash/restart) must not leave its real
answer stuck behind the "[No response was produced]" placeholder forever.

Root cause (2026-07-15, conversation 648e26c9 — see
~/.cowork/streams/648e26c9-*/turn_000000.jsonl): begin_assistant_turn()
writes the placeholder up front (write-ahead) and finalize_assistant_turn()
overwrites it once the turn ends, but that overwrite is an in-process call.
If the server dies between them, the placeholder sticks even though the
real text was already durably persisted in message_events. The existing
boot recovery (seal_orphan_buffers) only patches the JSONL replay buffer,
not this DB row.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlmodel import Session

from cowork.common.settings.app_settings import get_app_settings
from cowork.db.session import get_engine
from cowork.models.message import Message
from cowork.models.message_event import MessageEvent
from cowork.services.conversations import EMPTY_TURN_PLACEHOLDER, ConversationService
from cowork.services.projects import GENERAL_PROJECT_ID

_T0 = datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    engine = get_engine(get_app_settings().database.uri)
    with Session(engine) as s:
        yield s


def _add_user(session, conv_id, content):
    session.add(Message(conversation_id=conv_id, role="user", content=content, created_at=_T0))
    session.commit()


def _stuck_placeholder(session, conv_id, events: list[dict]) -> Message:
    """Simulate begin_assistant_turn() + streamed events + a crash before
    finalize_assistant_turn() ever runs."""
    message = Message(conversation_id=conv_id, role="assistant", content=EMPTY_TURN_PLACEHOLDER)
    session.add(message)
    session.commit()
    session.refresh(message)
    for seq, event_data in enumerate(events):
        session.add(MessageEvent(message_id=message.id, sequence_number=seq, event_data=event_data))
    session.commit()
    return message


def test_reconciles_placeholder_from_streamed_deltas(session):
    svc = ConversationService(session)
    conv = svc.create_conversation(topic="t", project_id=GENERAL_PROJECT_ID)
    _add_user(session, conv.id, "hi")
    message = _stuck_placeholder(session, conv.id, [
        {"type": "response.output_text.delta", "delta": "I'll search for the "},
        {"type": "response.output_text.delta", "delta": "Gmail tools to check your inbox."},
    ])

    repaired = svc.reconcile_stale_placeholders()

    assert repaired == 1
    session.refresh(message)
    assert message.content == "I'll search for the Gmail tools to check your inbox."


def test_reconciles_placeholder_prefers_completed_frame(session):
    svc = ConversationService(session)
    conv = svc.create_conversation(topic="t", project_id=GENERAL_PROJECT_ID)
    _add_user(session, conv.id, "hi")
    message = _stuck_placeholder(session, conv.id, [
        {"type": "response.output_text.delta", "delta": "partial"},
        {"type": "response.completed", "response": {"output": [
            {"content": [{"text": "the full real answer"}]}
        ]}},
    ])

    svc.reconcile_stale_placeholders()

    session.refresh(message)
    assert message.content == "the full real answer"


def test_leaves_genuine_empty_turns_alone(session):
    svc = ConversationService(session)
    conv = svc.create_conversation(topic="t", project_id=GENERAL_PROJECT_ID)
    _add_user(session, conv.id, "hi")
    message = _stuck_placeholder(session, conv.id, [{"type": "response.completed"}])

    repaired = svc.reconcile_stale_placeholders()

    assert repaired == 0
    session.refresh(message)
    assert message.content == EMPTY_TURN_PLACEHOLDER


def test_idempotent_on_already_finalized_messages(session):
    svc = ConversationService(session)
    conv = svc.create_conversation(topic="t", project_id=GENERAL_PROJECT_ID)
    _add_user(session, conv.id, "hi")
    svc.save_assistant_turn(conv.id, "already finalized text", [], harness="anton")

    repaired = svc.reconcile_stale_placeholders()

    assert repaired == 0
