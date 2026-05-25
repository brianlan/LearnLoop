from datetime import datetime, UTC

import pytest
from pydantic import ValidationError

from app.domain import (
    CoachingConversation,
    CoachingMessage,
    CoachingRole,
)


def test_coaching_message_validates_required_fields() -> None:
    message = CoachingMessage(
        role=CoachingRole.STUDENT,
        content="I don't understand this problem.",
    )

    assert message.role == CoachingRole.STUDENT
    assert message.content == "I don't understand this problem."
    assert message.whiteboard_dsl is None
    assert isinstance(message.created_at, datetime)


def test_coaching_message_accepts_whiteboard_dsl() -> None:
    message = CoachingMessage(
        role=CoachingRole.COACH,
        content="Let's draw this out.",
        whiteboard_dsl="board line 10 10 100 100",
    )

    assert message.whiteboard_dsl == "board line 10 10 100 100"


def test_coaching_message_rejects_invalid_role() -> None:
    with pytest.raises(ValidationError):
        CoachingMessage(
            role="teacher",
            content="Hello!",
        )


def test_coaching_conversation_validates_required_fields() -> None:
    conversation = CoachingConversation(
        problem_id="problem-1",
        user_id="user-1",
    )

    assert conversation.problem_id == "problem-1"
    assert conversation.user_id == "user-1"
    assert conversation.messages == []
    assert isinstance(conversation.created_at, datetime)
    assert isinstance(conversation.updated_at, datetime)


def test_coaching_conversation_adds_messages() -> None:
    conversation = CoachingConversation(
        problem_id="problem-1",
        user_id="user-1",
    )

    message1 = CoachingMessage(
        role=CoachingRole.STUDENT,
        content="Hello!",
    )
    message2 = CoachingMessage(
        role=CoachingRole.COACH,
        content="Hi there!",
    )

    conversation.add_message(message1)
    conversation.add_message(message2)

    assert len(conversation.messages) == 2
    assert conversation.messages[0].content == "Hello!"
    assert conversation.messages[1].content == "Hi there!"


def test_coaching_conversation_enforces_message_cap() -> None:
    conversation = CoachingConversation(
        problem_id="problem-1",
        user_id="user-1",
    )

    # Add 20 messages
    for i in range(20):
        conversation.add_message(
            CoachingMessage(
                role=CoachingRole.STUDENT,
                content=f"Message {i}",
            )
        )

    assert len(conversation.messages) == 20

    # Adding 21st message should fail
    with pytest.raises(ValueError, match="Conversation cannot have more than 20 messages"):
        conversation.add_message(
            CoachingMessage(
                role=CoachingRole.STUDENT,
                content="Message 21",
            )
        )


def test_coaching_conversation_clears_messages() -> None:
    conversation = CoachingConversation(
        problem_id="problem-1",
        user_id="user-1",
    )

    conversation.add_message(
        CoachingMessage(
            role=CoachingRole.STUDENT,
            content="Hello!",
        )
    )

    assert len(conversation.messages) == 1

    conversation.clear_messages()

    assert len(conversation.messages) == 0


def test_coaching_conversation_updates_updated_at_on_add() -> None:
    conversation = CoachingConversation(
        problem_id="problem-1",
        user_id="user-1",
    )

    original_updated_at = conversation.updated_at

    # Wait a tiny bit to ensure different timestamp
    import time
    time.sleep(0.001)

    conversation.add_message(
        CoachingMessage(
            role=CoachingRole.STUDENT,
            content="Hello!",
        )
    )

    assert conversation.updated_at > original_updated_at


def test_coaching_conversation_updates_updated_at_on_clear() -> None:
    conversation = CoachingConversation(
        problem_id="problem-1",
        user_id="user-1",
    )

    conversation.add_message(
        CoachingMessage(
            role=CoachingRole.STUDENT,
            content="Hello!",
        )
    )

    original_updated_at = conversation.updated_at

    # Wait a tiny bit to ensure different timestamp
    import time
    time.sleep(0.001)

    conversation.clear_messages()

    assert conversation.updated_at > original_updated_at
