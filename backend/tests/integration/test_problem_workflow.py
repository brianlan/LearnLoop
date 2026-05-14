from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_wf_problem_1_list_inspect_and_image_access(
    app: FastAPI,
    client: AsyncClient,
    register_and_login,
    create_problem_via_api,
) -> None:
    user = await register_and_login(client, username="wf-problem-1")

    created_problem = await create_problem_via_api(
        client,
        user_id=user["_id"],
        text="Solve x + 2 = 5",
        problem_type="fill-in-the-blank",
        correct_answer="3",
        tags=["algebra", "linear"],
        image_bytes=b"problem-one-image",
    )
    await create_problem_via_api(
        client,
        user_id=user["_id"],
        text="Explain inertia",
        problem_type="short-answer",
        correct_answer="resistance to change in motion",
        tags=["physics"],
        image_bytes=b"problem-two-image",
    )

    list_response = await client.get(
        "/api/v1/problems",
        params={"tag": "algebra", "type": "fill-in-the-blank", "page": 1, "pageSize": 10},
    )
    assert list_response.status_code == 200
    list_body = list_response.json()
    assert list_body["total"] == 1
    assert list_body["items"][0]["id"] == created_problem["id"]
    assert list_body["items"][0]["tracking"] == {
        "exposureCount": 0,
        "correctCount": 0,
        "failedCount": 0,
        "lastTestedAt": None,
        "lastAttemptCorrect": None,
    }

    detail_response = await client.get(f"/api/v1/problems/{created_problem['id']}")
    assert detail_response.status_code == 200
    detail_problem = detail_response.json()["problem"]
    assert detail_problem["text"] == "Solve x + 2 = 5"
    assert detail_problem["problemType"] == "fill-in-the-blank"
    assert detail_problem["correctAnswer"]["display"] == "3"
    assert detail_problem["imageUrl"] == f"/api/v1/problems/{created_problem['id']}/image"

    image_response = await client.get(f"/api/v1/problems/{created_problem['id']}/image")
    assert image_response.status_code == 200
    assert image_response.headers["content-type"] == "image/png"
    assert image_response.content == b"problem-one-image"


@pytest.mark.asyncio
async def test_wf_problem_2_edit_problem_keeps_submitted_exam_history_unchanged(
    client: AsyncClient,
    register_and_login,
    create_problem_via_api,
    find_exam_item,
) -> None:
    user = await register_and_login(client, username="wf-problem-2")

    problem = await create_problem_via_api(
        client,
        user_id=user["_id"],
        text="Original prompt",
        problem_type="fill-in-the-blank",
        correct_answer="4",
        tags=["math", "chapter-1"],
    )

    create_exam_response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})
    assert create_exam_response.status_code == 201
    exam = create_exam_response.json()["exam"]
    exam_item = find_exam_item(exam, problem_id=problem["id"])

    save_answer_response = await client.patch(
        f"/api/v1/exams/{exam['id']}/items/{exam_item['itemId']}/answer",
        json={"answer": "4"},
    )
    assert save_answer_response.status_code == 200

    submit_response = await client.post(f"/api/v1/exams/{exam['id']}/submit")
    assert submit_response.status_code == 200
    submitted_exam = submit_response.json()["exam"]
    assert submitted_exam["items"][0]["problem"]["text"] == "Original prompt"
    assert submitted_exam["items"][0]["problem"]["correctAnswer"]["display"] == "4"

    update_response = await client.patch(
        f"/api/v1/problems/{problem['id']}",
        json={
            "text": "Updated prompt",
            "problemType": "multi-choice",
            "graphDsl": "graph TD; A-->B",
            "correctAnswer": "B, A",
            "tags": ["updated", "chapter-2"],
        },
    )
    assert update_response.status_code == 200
    updated_problem = update_response.json()["problem"]
    assert updated_problem["text"] == "Updated prompt"
    assert updated_problem["problemType"] == "multi-choice"
    assert updated_problem["correctAnswer"] == {
        "display": "B, A",
        "normalizedText": "a,b",
        "normalizedSet": ["a", "b"],
        "format": "set",
    }

    problem_detail_response = await client.get(f"/api/v1/problems/{problem['id']}")
    assert problem_detail_response.status_code == 200
    assert problem_detail_response.json()["problem"]["text"] == "Updated prompt"

    history_detail_response = await client.get(f"/api/v1/exams/{exam['id']}")
    assert history_detail_response.status_code == 200
    history_exam = history_detail_response.json()["exam"]
    history_item = history_exam["items"][0]
    assert history_item["problem"]["text"] == "Original prompt"
    assert history_item["problem"]["problemType"] == "fill-in-the-blank"
    assert history_item["problem"]["correctAnswer"]["display"] == "4"


@pytest.mark.asyncio
async def test_wf_problem_3_soft_delete_hides_problem_but_keeps_history(
    client: AsyncClient,
    register_and_login,
    create_problem_via_api,
    find_exam_item,
) -> None:
    user = await register_and_login(client, username="wf-problem-3")

    problem = await create_problem_via_api(
        client,
        user_id=user["_id"],
        text="Delete me later",
        problem_type="fill-in-the-blank",
        correct_answer="7",
        tags=["deletable"],
    )

    create_exam_response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})
    assert create_exam_response.status_code == 201
    exam = create_exam_response.json()["exam"]
    exam_item = find_exam_item(exam, problem_id=problem["id"])

    await client.patch(
        f"/api/v1/exams/{exam['id']}/items/{exam_item['itemId']}/answer",
        json={"answer": "7"},
    )
    submit_response = await client.post(f"/api/v1/exams/{exam['id']}/submit")
    assert submit_response.status_code == 200

    delete_response = await client.delete(f"/api/v1/problems/{problem['id']}")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"ok": True}

    list_response = await client.get("/api/v1/problems")
    assert list_response.status_code == 200
    assert list_response.json()["total"] == 0

    detail_response = await client.get(f"/api/v1/problems/{problem['id']}")
    assert detail_response.status_code == 404
    assert detail_response.json() == {
        "error": {"code": "NOT_FOUND", "message": "Problem not found"}
    }

    second_exam_response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})
    assert second_exam_response.status_code == 422
    assert second_exam_response.json() == {
        "error": {
            "code": "NO_ELIGIBLE_PROBLEMS",
            "message": "No eligible problems available",
        }
    }

    history_response = await client.get("/api/v1/exams")
    assert history_response.status_code == 200
    assert history_response.json()["total"] == 1

    history_detail_response = await client.get(f"/api/v1/exams/{exam['id']}")
    assert history_detail_response.status_code == 200
    history_exam = history_detail_response.json()["exam"]
    assert history_exam["items"][0]["problem"]["text"] == "Delete me later"
    assert history_exam["items"][0]["problemId"] == problem["id"]
