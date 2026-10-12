from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from bson import ObjectId
from httpx import ASGITransport, AsyncClient

from app.domain.whiteboard import MAX_DSL_LENGTH
from app.main import create_app
from app.presentation.deps import get_current_user
from tests.api.conftest import make_user


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    application = create_app()
    application.dependency_overrides[get_current_user] = lambda: make_user(
        ObjectId(), "teacher"
    )
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as async_client:
        yield async_client


@pytest.mark.asyncio
async def test_validate_graph_dsl_accepts_compliant_dsl(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/graph-dsl/validate",
        json={"graphDsl": "var p = board.create('point', [0, 0]);"},
    )

    assert response.status_code == 200
    assert response.json() == {"valid": True, "issues": []}


@pytest.mark.asyncio
async def test_validate_graph_dsl_treats_empty_dsl_as_valid(client: AsyncClient) -> None:
    response = await client.post("/api/v1/graph-dsl/validate", json={"graphDsl": ""})

    assert response.status_code == 200
    assert response.json() == {"valid": True, "issues": []}


@pytest.mark.asyncio
async def test_validate_graph_dsl_returns_explain_reasons_verbatim(
    client: AsyncClient,
) -> None:
    response = await client.post(
        "/api/v1/graph-dsl/validate",
        json={"graphDsl": "board.create('rectangle', [0, 0, 1, 1]);"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["issues"] == [
        "rejected statement 'board.create('rectangle', [0, 0, 1, 1])': "
        "disallowed element type 'rectangle'"
    ]


@pytest.mark.asyncio
async def test_validate_graph_dsl_enforces_the_size_cap(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/graph-dsl/validate",
        json={"graphDsl": "x" * (MAX_DSL_LENGTH + 1)},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is False
    assert body["issues"] == [
        f"exceeds the maximum DSL length of {MAX_DSL_LENGTH} characters"
    ]


@pytest.mark.asyncio
async def test_validate_graph_dsl_applies_size_cap_after_preprocessing(
    client: AsyncClient,
) -> None:
    statement = "var p = board.create('point', [0, 0]);"
    comment_padding = "\n".join(f"// {'x' * 120}" for _ in range(200))
    raw = f"```js\n{statement}\n{comment_padding}\n```"
    assert len(raw) > MAX_DSL_LENGTH

    response = await client.post(
        "/api/v1/graph-dsl/validate",
        json={"graphDsl": raw},
    )

    assert response.status_code == 200
    assert response.json() == {"valid": True, "issues": []}


@pytest.mark.asyncio
async def test_validate_graph_dsl_requires_authentication() -> None:
    application = create_app()
    transport = ASGITransport(app=application)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.post(
            "/api/v1/graph-dsl/validate",
            json={"graphDsl": "var p = board.create('point', [0, 0]);"},
        )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHENTICATED"
