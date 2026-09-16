from datetime import datetime, UTC

import pytest
from bson import ObjectId

from app.domain.coaching.service import CoachingService, CoachingError
from app.domain.models import CoachingConversation, CoachingMessage, CoachingRole
from app.infrastructure.vlm.solution_coaching_client import CoachingVLMResult, SolutionCoachingVLMError
from app.solution_generation import compute_problem_context_hash
from tests.conftest import FakeCollection, FakeDatabase


# VLM-specific test double; kept local because it models the coaching VLM client,
# not Mongo/S3 storage shapes.
class FakeCoachingVLMClient:
    def __init__(self):
        self.error_to_raise = None
        self.result = CoachingVLMResult(
            model="test",
            text="hello from coach",
            whiteboard_dsl="dsl",
            raw_provider_response={}
        )
        self.calls = []

    async def send_message(self, request):
        self.calls.append(request)
        if self.error_to_raise:
            raise self.error_to_raise
        return self.result


def _problem(prob_id, user_id, *, text="prob text", answer="ans"):
    return {
        "_id": prob_id,
        "userId": user_id,
        "isDeleted": False,
        "text": text,
        "problemType": "short-answer",
        "graphDsl": None,
        "correctAnswer": {
            "display": answer,
            "normalizedText": answer,
            "normalizedSet": [],
            "format": "single",
        },
        "sourceImage": None,
    }


@pytest.mark.asyncio
async def test_get_conversation_not_found():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    service = CoachingService(db, vlm_client=client)
    conv = await service.get_conversation("prob1", "user1")
    assert conv is None


@pytest.mark.asyncio
async def test_clear_conversation():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    service = CoachingService(db, vlm_client=client)
    db["coaching_conversations"].seed({"problem_id": "prob1", "user_id": "user1"})
    await service.clear_conversation("prob1", "user1")
    assert await service.get_conversation("prob1", "user1") is None


@pytest.mark.asyncio
async def test_send_message_active_exam_blocked():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    service = CoachingService(db, vlm_client=client)

    prob_id = ObjectId()
    user_id = ObjectId()

    db["exams"].seed({
        "userId": user_id,
        "state": "in-progress",
        "items": [{"problemId": prob_id}]
    })

    with pytest.raises(CoachingError) as exc:
        await service.send_message(str(prob_id), str(user_id), "hello")
    assert exc.value.code == "ACTIVE_EXAM_RESTRICTION"


@pytest.mark.asyncio
async def test_send_message_success():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    service = CoachingService(db, vlm_client=client)

    prob_id = ObjectId()
    user_id = ObjectId()

    problem = _problem(prob_id, user_id)
    db["problems"].seed(problem)
    db["canonical_solutions"].seed({
        "problem_id": str(prob_id),
        "steps_markdown": "steps",
        "final_answer": "ans",
        "level_classification": "primary",
        "problem_context_hash": compute_problem_context_hash(problem),
    })

    conv = await service.send_message(str(prob_id), str(user_id), "help me")

    assert len(conv.messages) == 2
    assert conv.messages[0].role == CoachingRole.STUDENT
    assert conv.messages[0].content == "help me"
    assert conv.messages[1].role == CoachingRole.COACH
    assert conv.messages[1].content == "hello from coach"

    # check context
    req = client.calls[0]
    assert req.problem_text == "prob text"
    assert req.canonical_steps_markdown == "steps"
    assert req.canonical_final_answer == "ans"
    assert req.level_classification == "primary"


@pytest.mark.asyncio
async def test_send_message_changed_problem_ignores_stale_solution():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    service = CoachingService(db, vlm_client=client)

    prob_id = ObjectId()
    user_id = ObjectId()

    old_problem = _problem(prob_id, user_id, text="old text", answer="old ans")
    db["problems"].seed(_problem(prob_id, user_id, text="current text", answer="current ans"))
    db["canonical_solutions"].seed({
        "problem_id": str(prob_id),
        "steps_markdown": "old steps",
        "final_answer": "old ans",
        "level_classification": "middle-school",
        "problem_context_hash": compute_problem_context_hash(old_problem),
    })

    await service.send_message(str(prob_id), str(user_id), "help me")

    req = client.calls[0]
    assert req.canonical_steps_markdown == "No canonical steps available."
    assert req.correct_answer == "current ans"
    assert req.canonical_final_answer == "current ans"
    assert req.level_classification == "unknown"


@pytest.mark.asyncio
async def test_send_message_legacy_solution_without_hash_ignored():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    service = CoachingService(db, vlm_client=client)

    prob_id = ObjectId()
    user_id = ObjectId()

    db["problems"].seed(_problem(prob_id, user_id, answer="current ans"))
    db["canonical_solutions"].seed({
        "problem_id": str(prob_id),
        "steps_markdown": "legacy steps",
        "final_answer": "legacy ans",
        "level_classification": "primary",
    })

    await service.send_message(str(prob_id), str(user_id), "help me")

    req = client.calls[0]
    assert req.canonical_steps_markdown == "No canonical steps available."
    assert req.canonical_final_answer == "current ans"


@pytest.mark.asyncio
async def test_send_message_skipped_problem_no_attempt():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    service = CoachingService(db, vlm_client=client)

    prob_id = ObjectId()
    user_id = ObjectId()

    db["problems"].seed({"_id": prob_id, "userId": user_id, "isDeleted": False, "text": "prob text"})
    # no canonical solution

    conv = await service.send_message(str(prob_id), str(user_id), "help me")
    assert len(conv.messages) == 2

    req = client.calls[0]
    assert req.canonical_steps_markdown == "No canonical steps available."


@pytest.mark.asyncio
async def test_send_message_cap_exceeded():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    service = CoachingService(db, vlm_client=client)

    prob_id = ObjectId()
    user_id = ObjectId()

    db["problems"].seed({"_id": prob_id, "userId": user_id, "isDeleted": False})

    # create a conversation with 20 messages
    messages = [{"role": "student", "content": "hello"}] * 20
    db["coaching_conversations"].seed({
        "problem_id": str(prob_id), "user_id": str(user_id), "messages": messages
    })

    with pytest.raises(CoachingError) as exc:
        await service.send_message(str(prob_id), str(user_id), "hello")
    assert exc.value.code == "MESSAGE_CAP_EXCEEDED"


@pytest.mark.asyncio
async def test_send_message_vlm_failure():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    client.error_to_raise = SolutionCoachingVLMError("error", code="vlm-error", retryable=True)
    service = CoachingService(db, vlm_client=client)

    prob_id = ObjectId()
    user_id = ObjectId()

    db["problems"].seed({"_id": prob_id, "userId": user_id, "isDeleted": False})

    with pytest.raises(CoachingError) as exc:
        await service.send_message(str(prob_id), str(user_id), "hello")
    assert exc.value.code == "VLM_FAILURE"
    assert exc.value.status_code == 503


@pytest.mark.asyncio
async def test_send_message_persists_reasoning_content():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    client.result = CoachingVLMResult(
        model="test",
        text="coach reply",
        whiteboard_dsl=None,
        reasoning_content="step by step thinking",
        raw_provider_response={}
    )
    service = CoachingService(db, vlm_client=client)

    prob_id = ObjectId()
    user_id = ObjectId()

    db["problems"].seed({"_id": prob_id, "userId": user_id, "isDeleted": False, "text": "prob text"})
    db["canonical_solutions"].seed({"problem_id": str(prob_id), "steps_markdown": "steps", "final_answer": "ans"})

    conv = await service.send_message(str(prob_id), str(user_id), "help me")

    assert len(conv.messages) == 2
    assert conv.messages[1].role == CoachingRole.COACH
    assert conv.messages[1].content == "coach reply"
    assert conv.messages[1].reasoning_content == "step by step thinking"


@pytest.mark.asyncio
async def test_send_message_reasoning_content_none_when_absent():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    client.result = CoachingVLMResult(
        model="test",
        text="coach reply",
        whiteboard_dsl=None,
        raw_provider_response={}
    )
    service = CoachingService(db, vlm_client=client)

    prob_id = ObjectId()
    user_id = ObjectId()

    db["problems"].seed({"_id": prob_id, "userId": user_id, "isDeleted": False, "text": "prob text"})
    db["canonical_solutions"].seed({"problem_id": str(prob_id), "steps_markdown": "steps", "final_answer": "ans"})

    conv = await service.send_message(str(prob_id), str(user_id), "help me")

    assert len(conv.messages) == 2
    assert conv.messages[1].role == CoachingRole.COACH
    assert conv.messages[1].content == "coach reply"
    assert conv.messages[1].reasoning_content is None
