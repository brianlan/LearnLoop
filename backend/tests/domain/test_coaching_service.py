from datetime import datetime, UTC
from copy import deepcopy

import pytest
from bson import ObjectId

from app.domain.coaching.service import CoachingService, CoachingError
from app.domain.models import CoachingConversation, CoachingMessage, CoachingRole
from app.infrastructure.vlm.solution_coaching_client import CoachingVLMResult, SolutionCoachingVLMError


class FakeDatabase:
    def __init__(self):
        self.cols = {
            "exams": FakeCollection(),
            "problems": FakeCollection(),
            "canonical_solutions": FakeCollection(),
            "practice_attempts": FakeCollection(),
            "coaching_conversations": FakeCollection()
        }

    def __getitem__(self, name):
        return self.cols[name]


class FakeCollection:
    def __init__(self):
        self.docs = []

    def seed(self, doc):
        if "_id" not in doc:
            doc["_id"] = ObjectId()
        self.docs.append(deepcopy(doc))

    async def count_documents(self, query):
        return len([d for d in self.docs if self._match(d, query)])

    async def find_one(self, query, sort=None):
        docs = [d for d in self.docs if self._match(d, query)]
        if not docs:
            return None
        if sort:
            docs.sort(key=lambda x: x.get(sort[0][0], datetime.min), reverse=(sort[0][1] == -1))
        return deepcopy(docs[0])

    async def delete_one(self, query):
        docs = [d for d in self.docs if not self._match(d, query)]
        self.docs = docs

    async def update_one(self, query, update, upsert=False):
        doc = await self.find_one(query)
        if doc:
            for k, v in update.get("$set", {}).items():
                doc[k] = v
            # update in place
            for i, d in enumerate(self.docs):
                if self._match(d, query):
                    self.docs[i] = doc
                    break
        elif upsert:
            new_doc = deepcopy(query)
            for k, v in update.get("$set", {}).items():
                new_doc[k] = v
            self.seed(new_doc)

    def _match(self, doc, query):
        for k, v in query.items():
            if k == "items.problemId":
                # mock logic for this specific query
                if "items" in doc and any(v == item.get("problemId") for item in doc["items"]):
                    continue
                return False
            if doc.get(k) != v:
                return False
        return True


class FakeCoachingVLMClient:
    def __init__(self):
        self.error_to_raise = None
        self.result = CoachingVLMResult(
            prompt_version="1",
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


@pytest.mark.asyncio
async def test_get_conversation_not_found():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    service = CoachingService(db, client)
    conv = await service.get_conversation("prob1", "user1")
    assert conv is None

@pytest.mark.asyncio
async def test_clear_conversation():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    service = CoachingService(db, client)
    db.cols["coaching_conversations"].seed({"problem_id": "prob1", "user_id": "user1"})
    await service.clear_conversation("prob1", "user1")
    assert await service.get_conversation("prob1", "user1") is None

@pytest.mark.asyncio
async def test_send_message_active_exam_blocked():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    service = CoachingService(db, client)
    
    prob_id = ObjectId()
    user_id = ObjectId()

    db.cols["exams"].seed({
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
    service = CoachingService(db, client)

    prob_id = ObjectId()
    user_id = ObjectId()

    db.cols["problems"].seed({"_id": prob_id, "userId": user_id, "isDeleted": False, "text": "prob text"})
    db.cols["canonical_solutions"].seed({"problem_id": str(prob_id), "steps_markdown": "steps", "final_answer": "ans"})

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

@pytest.mark.asyncio
async def test_send_message_skipped_problem_no_attempt():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    service = CoachingService(db, client)

    prob_id = ObjectId()
    user_id = ObjectId()

    db.cols["problems"].seed({"_id": prob_id, "userId": user_id, "isDeleted": False, "text": "prob text"})
    # no canonical solution

    conv = await service.send_message(str(prob_id), str(user_id), "help me")
    assert len(conv.messages) == 2

    req = client.calls[0]
    assert req.canonical_steps_markdown == "No canonical steps available."

@pytest.mark.asyncio
async def test_send_message_cap_exceeded():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    service = CoachingService(db, client)
    
    prob_id = ObjectId()
    user_id = ObjectId()
    
    db.cols["problems"].seed({"_id": prob_id, "userId": user_id, "isDeleted": False})
    
    # create a conversation with 20 messages
    messages = [{"role": "student", "content": "hello"}] * 20
    db.cols["coaching_conversations"].seed({
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
    service = CoachingService(db, client)
    
    prob_id = ObjectId()
    user_id = ObjectId()
    
    db.cols["problems"].seed({"_id": prob_id, "userId": user_id, "isDeleted": False})
    
    with pytest.raises(CoachingError) as exc:
        await service.send_message(str(prob_id), str(user_id), "hello")
    assert exc.value.code == "VLM_FAILURE"
    assert exc.value.status_code == 503

@pytest.mark.asyncio
async def test_send_message_persists_reasoning_content():
    db = FakeDatabase()
    client = FakeCoachingVLMClient()
    client.result = CoachingVLMResult(
        prompt_version="1",
        model="test",
        text="coach reply",
        whiteboard_dsl=None,
        reasoning_content="step by step thinking",
        raw_provider_response={}
    )
    service = CoachingService(db, client)

    prob_id = ObjectId()
    user_id = ObjectId()

    db.cols["problems"].seed({"_id": prob_id, "userId": user_id, "isDeleted": False, "text": "prob text"})
    db.cols["canonical_solutions"].seed({"problem_id": str(prob_id), "steps_markdown": "steps", "final_answer": "ans"})

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
        prompt_version="1",
        model="test",
        text="coach reply",
        whiteboard_dsl=None,
        raw_provider_response={}
    )
    service = CoachingService(db, client)

    prob_id = ObjectId()
    user_id = ObjectId()

    db.cols["problems"].seed({"_id": prob_id, "userId": user_id, "isDeleted": False, "text": "prob text"})
    db.cols["canonical_solutions"].seed({"problem_id": str(prob_id), "steps_markdown": "steps", "final_answer": "ans"})

    conv = await service.send_message(str(prob_id), str(user_id), "help me")

    assert conv.messages[1].reasoning_content is None
