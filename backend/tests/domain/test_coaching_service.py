from base64 import b64encode
from datetime import datetime, UTC

import pytest
from bson import ObjectId

from app.domain.coaching.service import CoachingService, CoachingError
from app.domain.models import CoachingConversation, CoachingMessage, CoachingRole
from app.infrastructure.vlm.solution_coaching_client import CoachingVLMResult, SolutionCoachingVLMError
from app.solution_generation import compute_problem_context_hash
from tests.conftest import FakeCollection, FakeDatabase, FakeStorage


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
    assert client.calls == []


@pytest.mark.asyncio
async def test_send_message_cap_rejected_at_nineteen_messages_without_vlm_call():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    service = CoachingService(db, vlm_client=client)

    prob_id = ObjectId()
    user_id = ObjectId()

    db["problems"].seed({"_id": prob_id, "userId": user_id, "isDeleted": False, "text": "prob text"})

    # 19 existing messages leave room for only one of the two messages this turn appends.
    messages = [{"role": "student", "content": "hello"}] * 19
    db["coaching_conversations"].seed({
        "problem_id": str(prob_id), "user_id": str(user_id), "messages": messages
    })

    with pytest.raises(CoachingError) as exc:
        await service.send_message(str(prob_id), str(user_id), "hello")
    assert exc.value.code == "MESSAGE_CAP_EXCEEDED"
    assert client.calls == []


@pytest.mark.asyncio
async def test_send_message_completes_turn_at_eighteen_messages():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    service = CoachingService(db, vlm_client=client)

    prob_id = ObjectId()
    user_id = ObjectId()

    db["problems"].seed({"_id": prob_id, "userId": user_id, "isDeleted": False, "text": "prob text"})
    db["canonical_solutions"].seed({"problem_id": str(prob_id), "steps_markdown": "steps", "final_answer": "ans"})

    # 18 existing messages leave exactly the two slots this turn uses.
    messages = [{"role": "student", "content": "hello"}] * 18
    db["coaching_conversations"].seed({
        "problem_id": str(prob_id), "user_id": str(user_id), "messages": messages
    })

    conv = await service.send_message(str(prob_id), str(user_id), "help me")

    assert len(conv.messages) == 20
    assert len(client.calls) == 1


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


def _seed_problem_with_context(db, prob_id, user_id, *, graph_dsl=None, source_image=None):
    problem = _problem(prob_id, user_id)
    problem["graphDsl"] = graph_dsl
    problem["sourceImage"] = source_image
    db["problems"].seed(problem)
    db["canonical_solutions"].seed({
        "problem_id": str(prob_id),
        "steps_markdown": "steps",
        "final_answer": "ans",
        "level_classification": "primary",
        "problem_context_hash": compute_problem_context_hash(problem),
    })


@pytest.mark.asyncio
async def test_send_message_includes_graph_dsl_and_source_image():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    storage = FakeStorage()
    storage.seed("media-bucket", "problems/img.jpg", b"JPEGDATA")
    service = CoachingService(db, vlm_client=client, storage=storage)

    prob_id = ObjectId()
    user_id = ObjectId()
    _seed_problem_with_context(
        db,
        prob_id,
        user_id,
        graph_dsl="board.create('point', [0, 0]);",
        source_image={
            "bucket": "media-bucket",
            "objectKey": "problems/img.jpg",
            "contentType": "image/jpeg",
        },
    )

    await service.send_message(str(prob_id), str(user_id), "help me")

    req = client.calls[0]
    assert req.graph_dsl == "board.create('point', [0, 0]);"
    assert req.image_media_type == "image/jpeg"
    assert req.image_base64 == b64encode(b"JPEGDATA").decode("ascii")
    assert storage.get_calls == [("media-bucket", "problems/img.jpg")]


@pytest.mark.asyncio
async def test_send_message_missing_source_image_degrades_to_text_only():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    storage = FakeStorage()  # object never seeded -> StorageObjectNotFoundError
    service = CoachingService(db, vlm_client=client, storage=storage)

    prob_id = ObjectId()
    user_id = ObjectId()
    _seed_problem_with_context(
        db,
        prob_id,
        user_id,
        source_image={"bucket": "b", "objectKey": "missing.png", "contentType": "image/png"},
    )

    conv = await service.send_message(str(prob_id), str(user_id), "help me")

    assert len(conv.messages) == 2
    req = client.calls[0]
    assert req.image_base64 is None
    assert req.problem_text == "prob text"


@pytest.mark.asyncio
async def test_send_message_storage_error_degrades_to_text_only():
    class ExplodingStorage:
        def get_object(self, bucket, object_key):
            raise RuntimeError("storage down")

    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    service = CoachingService(db, vlm_client=client, storage=ExplodingStorage())

    prob_id = ObjectId()
    user_id = ObjectId()
    _seed_problem_with_context(
        db,
        prob_id,
        user_id,
        source_image={"bucket": "b", "objectKey": "img.png", "contentType": "image/png"},
    )

    conv = await service.send_message(str(prob_id), str(user_id), "help me")

    assert len(conv.messages) == 2
    assert client.calls[0].image_base64 is None


@pytest.mark.asyncio
async def test_send_message_history_contains_only_role_and_text():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    service = CoachingService(db, vlm_client=client)

    prob_id = ObjectId()
    user_id = ObjectId()
    db["problems"].seed(_problem(prob_id, user_id))
    db["coaching_conversations"].seed({
        "problem_id": str(prob_id),
        "user_id": str(user_id),
        "messages": [
            {"role": "student", "content": "先看第一步"},
            {"role": "coach", "content": "先看已知条件"},
        ],
    })

    await service.send_message(str(prob_id), str(user_id), "再来一个提示")

    history = client.calls[0].conversation_history
    assert [message.model_dump() for message in history] == [
        {"role": "student", "text": "先看第一步"},
        {"role": "coach", "text": "先看已知条件"},
    ]
