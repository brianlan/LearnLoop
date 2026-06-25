import pytest
import pytest_asyncio
from copy import deepcopy
from httpx import ASGITransport, AsyncClient
from fastapi import FastAPI
from bson import ObjectId
from unittest.mock import patch, AsyncMock

from app.domain.models import CoachingRole
from app.infrastructure.vlm.solution_coaching_client import CoachingVLMResult
from app.infrastructure.storage.mongo import COACHING_CONVERSATIONS_COLLECTION
from app.main import create_app
from app.presentation.deps import get_current_user, get_database
from tests.api.conftest import FakeDatabase

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
    with patch("app.infrastructure.vlm.solution_coaching_client.CoachingVLMClient.send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = CoachingVLMResult(
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


@pytest.mark.asyncio
async def test_send_message_returns_reasoning_content(client: AsyncClient, setup_problem: str):
    with patch("app.infrastructure.vlm.solution_coaching_client.CoachingVLMClient.send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = CoachingVLMResult(
            model="test",
            text="coach response",
            whiteboard_dsl=None,
            reasoning_content="step by step thinking",
            raw_provider_response={}
        )

        response = await client.post(
            f"/api/v1/coaching/{setup_problem}/messages",
            json={"message": "help me understand"}
        )

        assert response.status_code == 200
        data = response.json()
        assert data["messages"][1]["reasoning_content"] == "step by step thinking"


@pytest.mark.asyncio
async def test_send_message_reasoning_content_null_when_absent(client: AsyncClient, setup_problem: str):
    with patch("app.infrastructure.vlm.solution_coaching_client.CoachingVLMClient.send_message", new_callable=AsyncMock) as mock_send:
        mock_send.return_value = CoachingVLMResult(
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
        assert data["messages"][1].get("reasoning_content") is None
