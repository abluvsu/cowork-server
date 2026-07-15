"""GET /conversations/{id}/items (ConversationService.get_messages) must
return messages in true wall-clock order, even when an earlier turn ended
with the empty-turn placeholder.

Regression for the "placeholder can appear last" gotcha (2026-07-15,
conversation 648e26c9): a failed/empty turn 0 placeholder, followed by two
later turns with real text, must come back placeholder-first — never with
the placeholder sorted after a real answer that happened later.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlmodel import Session

from cowork.common.settings.app_settings import get_app_settings
from cowork.db.session import get_engine
from cowork.models.message import Message
from cowork.services.conversations import EMPTY_TURN_PLACEHOLDER, ConversationService
from cowork.services.projects import GENERAL_PROJECT_ID

_T0 = datetime(2020, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture()
def session():
    engine = get_engine(get_app_settings().database.uri)
    with Session(engine) as s:
        yield s


def _add(session, conv_id, role, content, at):
    session.add(Message(conversation_id=conv_id, role=role, content=content, created_at=at))
    session.commit()


def test_placeholder_stays_first_when_later_turns_succeed(session):
    svc = ConversationService(session)
    conv = svc.create_conversation(topic="t", project_id=GENERAL_PROJECT_ID)

    _add(session, conv.id, "user", "check my inbox", _T0)
    _add(session, conv.id, "assistant", EMPTY_TURN_PLACEHOLDER, _T0 + timedelta(seconds=15))
    _add(session, conv.id, "user", "try again", _T0 + timedelta(minutes=12))
    _add(session, conv.id, "assistant", "the gmail server is still connecting", _T0 + timedelta(minutes=12, seconds=26))
    _add(session, conv.id, "user", "now?", _T0 + timedelta(minutes=17))
    _add(session, conv.id, "assistant", "here is your latest email", _T0 + timedelta(minutes=17, seconds=20))

    contents = [m["content"] for m in svc.get_messages(conv.id)]

    assert contents == [
        "check my inbox",
        EMPTY_TURN_PLACEHOLDER,
        "try again",
        "the gmail server is still connecting",
        "now?",
        "here is your latest email",
    ]


def test_same_second_tiebreak_keeps_user_before_its_own_assistant_reply(session):
    svc = ConversationService(session)
    conv = svc.create_conversation(topic="t", project_id=GENERAL_PROJECT_ID)

    same_second = _T0 + timedelta(minutes=5)
    _add(session, conv.id, "user", "quick question", same_second)
    _add(session, conv.id, "assistant", "quick answer", same_second)

    contents = [m["content"] for m in svc.get_messages(conv.id)]
    assert contents == ["quick question", "quick answer"]
