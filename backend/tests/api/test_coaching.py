import pytest
import pytest_asyncio
from copy import deepcopy
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from bson import ObjectId
from unittest.mock import patch, AsyncMock

from app.domain.models import CoachingRole
from app.infrastructure.llm.client import CoachingLLMResult
from app.infrastructure.storage.mongo import COACHING_CONVERSATIONS_COLLECTION
from app.main import create_app
from app.presentation.deps import get_current_user, get_database

class FakeCursor:
    def __init__(self, documents):
        self._documents = [deepcopy(doc) for doc in documents]
    async def to_list(self, length=None):
        return [deepcopy(doc) for doc in self._documents]

class FakeCollection:
    def __init__(self):
        self._documents = []

    def seed(self, *documents):
        self._documents.extend(deepcopy(list(documents)))

    async def find_one(self, query, sort=None):
        docs = []
        for doc in self._documents:
            if all(doc.get(k) == v for k, v in query.items() if k != "items.problemId"):
                if "items.problemId" in query:
                    val = query["items.problemId"]
                    if not any(val == item.get("problemId") for item in doc.get("items", [])):
                        continue
                docs.append(deepcopy(doc))
        if not docs:
            return None
        if sort:
            docs.sort(key=lambda x: x.get(sort[0][0]), reverse=(sort[0][1] == -1))
        return docs[0]

    def find(self, query):
        docs = [d for d in self._documents if all(d.get(k) == v for k, v in query.items())]
        return FakeCursor(docs)

    async def insert_one(self, document):
        doc = deepcopy(document)
        if "_id" not in doc:
            doc["_id"] = ObjectId()
        self._documents.append(doc)
        class Ret:
            inserted_id = doc["_id"]
        return Ret()

    async def update_one(self, query, update, upsert=False):
        for doc in self._documents:
            if all(doc.get(k) == v for k, v in query.items()):
                for k, v in update.get("$set", {}).items():
                    doc[k] = deepcopy(v)
                return
        if upsert:
            new_doc = deepcopy(query)
            for k, v in update.get("$set", {}).items():
                new_doc[k] = deepcopy(v)
            if "_id" not in new_doc:
                new_doc["_id"] = ObjectId()
            self._documents.append(new_doc)

    async def delete_one(self, query):
        self._documents = [d for d in self._documents if not all(d.get(k) == v for k, v in query.items())]

    async def count_documents(self, query):
        count = 0
        for doc in self._documents:
            match = True
            for k, v in query.items():
                if k == "items.problemId":
                    if not any(v == item.get("problemId") for item in doc.get("items", [])):
                        match = False
                        break
                elif doc.get(k) != v:
                    match = False
                    break
            if match:
                count += 1
        return count

class FakeDatabase:
    def __init__(self):
        self._collections = {
            "problems": FakeCollection(),
            "exams": FakeCollection(),
            "canonical_solutions": FakeCollection(),
            "practice_attempts": FakeCollection(),
            "coaching_conversations": FakeCollection(),
        }
    def __getitem__(self, name):
        return self._collections[name]

@pytest_asyncio.fixture
async def coaching_app() -> FastAPI:
    application = create_app()
    database = FakeDatabase()
    primary_user = {"_id": ObjectId(), "username": "student1"}
    secondary_user = {"_id": ObjectId(), "username": "student2"}
    
    application.state.fake_database = database
    application.state.primary_user = primary_user
    application.state.secondary_user = secondary_user

    application.dependency_overrides[get_database] = lambda: database
    application.dependency_overrides[get_current_user] = lambda: deepcopy(primary_user)
    return application

@pytest_asyncio.fixture
async def client(coaching_app: FastAPI):
    transport = ASGITransport(app=coaching_app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client

@pytest.fixture
def problem_id():
    return str(ObjectId())

@pytest.fixture
def setup_problem(coaching_app, problem_id):
    db = coaching_app.state.fake_database
    user_id = coaching_app.state.primary_user["_id"]
    db["problems"].seed({"_id": ObjectId(problem_id), "userId": user_id, "isDeleted": False})
    return problem_id


@pytest.mark.asyncio
async def test_get_conversation_not_found(client: AsyncClient, setup_problem: str):
    response = await client.get(f"/api/v1/coaching/{setup_problem}/conversation")
    assert response.status_code == 200
    data = response.json()
    assert data["problem_id"] == setup_problem
    assert data["messages"] == []


@pytest.mark.asyncio
async def test_send_message_success(client: AsyncClient, setup_problem: str):
    with patch("app.infrastructure.llm.client.CoachingLLMClient.send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = CoachingLLMResult(
            prompt_version="1",
            model="test",
            text="coach response",
            whiteboard_dsl=None,
            raw_provider_response={}
        )
        
        response = await client.post(
            f"/api/v1/coaching/{setup_problem}/messages", 
            json={"message": "help me understand"}
        )
        
        assert response.status_code == 200
        data = response.json()
        assert len(data["messages"]) == 2
        assert data["messages"][0]["role"] == CoachingRole.STUDENT.value
        assert data["messages"][0]["content"] == "help me understand"
        assert data["messages"][1]["role"] == CoachingRole.COACH.value
        assert data["messages"][1]["content"] == "coach response"


@pytest.mark.asyncio
async def test_clear_conversation(client: AsyncClient, coaching_app: FastAPI, setup_problem: str):
    db = coaching_app.state.fake_database
    user_id = coaching_app.state.primary_user["_id"]
    
    db[COACHING_CONVERSATIONS_COLLECTION].seed({
        "problem_id": setup_problem,
        "user_id": str(user_id),
        "messages": [{"role": "student", "content": "hello"}]
    })
    
    response = await client.delete(f"/api/v1/coaching/{setup_problem}/conversation")
    assert response.status_code == 204
    
    doc = await db[COACHING_CONVERSATIONS_COLLECTION].find_one({"problem_id": setup_problem})
    assert doc is None


@pytest.mark.asyncio
async def test_send_message_active_exam_blocked(client: AsyncClient, coaching_app: FastAPI, setup_problem: str):
    db = coaching_app.state.fake_database
    user_id = coaching_app.state.primary_user["_id"]
    
    db["exams"].seed({
        "userId": user_id,
        "state": "in-progress",
        "items": [{"problemId": ObjectId(setup_problem)}]
    })
    
    response = await client.post(
        f"/api/v1/coaching/{setup_problem}/messages", 
        json={"message": "help me understand"}
    )
    
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ACTIVE_EXAM_RESTRICTION"


@pytest.mark.asyncio
async def test_access_other_user_conversation(client: AsyncClient, coaching_app: FastAPI):
    db = coaching_app.state.fake_database
    other_user_id = coaching_app.state.secondary_user["_id"]
    other_prob_id = ObjectId()
    
    db["problems"].seed({
        "_id": other_prob_id,
        "userId": other_user_id,
        "isDeleted": False
    })
    
    response = await client.get(f"/api/v1/coaching/{str(other_prob_id)}/conversation")
    assert response.status_code == 404

