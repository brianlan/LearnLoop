from __future__ import annotations

from typing import Any, Literal, cast

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.infrastructure.vlm.client import ClassificationResult, ExtractionResult, VLMError


async def register_and_login(
    client: AsyncClient,
    *,
    username: str,
    password: str = "secret",
) -> None:
    register_response = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": password},
    )
    assert register_response.status_code == 201

    login_response = await client.post(
        "/api/v1/auth/login",
        json={"username": username, "password": password},
    )
    assert login_response.status_code == 200


def make_extraction_result(
    *,
    text: str = "What is 2 + 2?",
    problem_type: Literal[
        "single-choice", "multi-choice", "fill-in-the-blank", "short-answer"
    ] = "short-answer",
    graph_dsl: str | None = None,
    model: str = "gpt-4.1-mini",
) -> ExtractionResult:
    return ExtractionResult(
        request_type="ingestion",
        model=model,
        text=text,
        problem_type=problem_type,
        graph_dsl=graph_dsl,
        provider_metadata={"provider": "fake-vlm"},
        raw_provider_response={
            "text": text,
            "problemType": problem_type,
            "graphDsl": graph_dsl,
            "providerMetadata": {"provider": "fake-vlm"},
        },
    )


@pytest.mark.asyncio
async def test_wf_ing_1_clipboard_ingestion_creates_preview_and_confirms_problem(
    app: FastAPI,
    client: AsyncClient,
    database: Any,
    storage: Any,
    helper_vlm_client: Any,
    math_ingestion_vlm_client: Any,
    png_bytes: bytes,
) -> None:
    await register_and_login(client, username="student1")
    app.state.sync_wait_seconds = 1.0
    helper_vlm_client.responses = [
        ClassificationResult(
            request_type="subject-classification",
            model="gpt-4.1-mini",
            subject="math",
            confidence=0.95,
            reason="Contains math notation",
            provider_metadata={},
            raw_provider_response={},
        )
    ]
    math_ingestion_vlm_client.responses = [
        make_extraction_result(
            text="What is 2 + 2?",
            problem_type="short-answer",
        )
    ]

    create_response = await client.post(
        "/api/v1/ingestion-previews",
        files={"image": ("clipboard.png", png_bytes, "image/png")},
    )

    assert create_response.status_code == 201
    create_body = create_response.json()["preview"]
    assert create_body["status"] == "ready"
    assert create_body["draft"]["text"] == "What is 2 + 2?"
    assert create_body["draft"]["problemType"] == "short-answer"
    assert create_body["extraction"]["success"] is True
    assert create_body["sourceImage"]["contentType"] == "image/png"
    assert create_body["sourceImage"]["sizeBytes"] == len(png_bytes)
    assert len(storage.put_calls) == 1
    assert storage.put_calls[0][3] == png_bytes

    preview_id = create_body["id"]
    patch_response = await client.patch(
        f"/api/v1/ingestion-previews/{preview_id}",
        json={
            "text": "  Confirmed prompt text  ",
            "problemType": "short-answer",
            "correctAnswer": " 4 ",
            "tags": ["math", " math ", "chapter-1"],
        },
    )

    assert patch_response.status_code == 200
    patched_preview = patch_response.json()["preview"]
    assert patched_preview["draft"] == {
        "text": "Confirmed prompt text",
        "problemType": "short-answer",
        "graphDsl": None,
        "correctAnswer": "4",
        "tags": ["math", "chapter-1"],
        "subject": "math",
    }

    confirm_response = await client.post(f"/api/v1/ingestion-previews/{preview_id}/confirm")

    assert confirm_response.status_code == 201
    problem = confirm_response.json()["problem"]
    assert problem["text"] == "Confirmed prompt text"
    assert problem["problemType"] == "short-answer"
    assert problem["correctAnswer"] == {
        "display": "4",
        "normalizedText": "4",
        "normalizedSet": [],
        "format": "single",
    }
    assert problem["tags"] == ["math", "chapter-1"]

    preview_document_id = database["ingestion_previews"]._documents[0]["_id"]
    stored_preview = await database["ingestion_previews"].find_one({"_id": preview_document_id})
    assert stored_preview is not None
    assert stored_preview["status"] == "confirmed"

    problem_document_id = database["problems"]._documents[0]["_id"]
    stored_problem = await database["problems"].find_one({"_id": problem_document_id})
    assert stored_problem is not None
    assert stored_problem["origin"]["previewId"] == preview_id


@pytest.mark.asyncio
async def test_wf_ing_2_retry_failed_extraction_transitions_preview_to_ready(
    app: FastAPI,
    client: AsyncClient,
    database: Any,
    storage: Any,
    helper_vlm_client: Any,
    math_ingestion_vlm_client: Any,
    png_bytes: bytes,
) -> None:
    await register_and_login(client, username="student1")
    app.state.sync_wait_seconds = 1.0
    helper_vlm_client.responses = [
        ClassificationResult(
            request_type="subject-classification",
            model="gpt-4.1-mini",
            subject="math",
            confidence=0.95,
            reason="Contains math notation",
            provider_metadata={},
            raw_provider_response={},
        )
    ]
    math_ingestion_vlm_client.responses = [
        VLMError(
            "VLM request timed out",
            code="vlm-timeout",
            retryable=True,
            raw_provider_response={"detail": "timeout"},
        )
    ]

    create_response = await client.post(
        "/api/v1/ingestion-previews",
        files={"image": ("clipboard.png", png_bytes, "image/png")},
    )

    assert create_response.status_code == 201
    failed_preview = create_response.json()["preview"]
    assert failed_preview["status"] == "vlm-failed"
    assert failed_preview["extraction"]["success"] is False
    assert failed_preview["extraction"]["failureCode"] == "vlm-timeout"
    assert failed_preview["extraction"]["failureMessage"] == "VLM request timed out"

    preview_id = failed_preview["id"]
    stored_preview = cast(dict[str, Any], app.state.fake_database["ingestion_previews"]._documents[0])
    assert storage._objects[
        (stored_preview["sourceImage"]["bucket"], stored_preview["sourceImage"]["objectKey"])
    ] == png_bytes

    math_ingestion_vlm_client.responses = [make_extraction_result(text="Retry succeeded", problem_type="short-answer")]

    retry_response = await client.post(f"/api/v1/ingestion-previews/{preview_id}/retry")

    assert retry_response.status_code == 200
    retried_preview = retry_response.json()["preview"]
    assert retried_preview["status"] == "ready"
    assert retried_preview["draft"]["text"] == "Retry succeeded"
    assert retried_preview["draft"]["problemType"] == "short-answer"
    assert retried_preview["extraction"]["success"] is True
    assert storage.get_calls == [
        (stored_preview["sourceImage"]["bucket"], stored_preview["sourceImage"]["objectKey"]),
        (stored_preview["sourceImage"]["bucket"], stored_preview["sourceImage"]["objectKey"])
    ]

    refreshed_preview = await database["ingestion_previews"].find_one({"_id": stored_preview["_id"]})
    assert refreshed_preview is not None
    assert refreshed_preview["status"] == "ready"
