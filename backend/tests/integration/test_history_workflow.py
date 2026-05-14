from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_wf_history_1_review_tracking_history_and_immutable_snapshots(
    client: AsyncClient,
    register_and_login,
    create_problem_via_api,
    find_exam_item,
) -> None:
    user = await register_and_login(client, username="wf-history-1")

    problem = await create_problem_via_api(
        client,
        user_id=user["_id"],
        text="Historical original text",
        problem_type="fill-in-the-blank",
        correct_answer="9",
        tags=["history-test", "tracking"],
    )

    create_exam_response = await client.post("/api/v1/exams", json={"maxProblemCount": 1})
    assert create_exam_response.status_code == 201
    exam = create_exam_response.json()["exam"]
    item = find_exam_item(exam, problem_id=problem["id"])

    await client.patch(
        f"/api/v1/exams/{exam['id']}/items/{item['itemId']}/answer",
        json={"answer": "9"},
    )
    submit_response = await client.post(f"/api/v1/exams/{exam['id']}/submit")
    assert submit_response.status_code == 200

    tracking_response = await client.get(f"/api/v1/problems/{problem['id']}/tracking")
    assert tracking_response.status_code == 200
    tracking_body = tracking_response.json()
    assert tracking_body["problemId"] == problem["id"]
    assert tracking_body["tracking"] == {
        "exposureCount": 1,
        "correctCount": 1,
        "failedCount": 0,
        "lastTestedAt": tracking_body["tracking"]["lastTestedAt"],
        "lastAttemptCorrect": True,
    }
    assert tracking_body["tracking"]["lastTestedAt"] is not None

    history_list_response = await client.get("/api/v1/exams", params={"page": 1, "pageSize": 10})
    assert history_list_response.status_code == 200
    history_list = history_list_response.json()
    assert history_list["total"] == 1
    assert history_list["items"][0]["id"] == exam["id"]
    assert history_list["items"][0]["summary"] == {
        "totalProblems": 1,
        "answeredProblems": 1,
        "gradedProblems": 1,
        "pendingProblems": 0,
        "correctProblems": 1,
        "failedProblems": 0,
        "score": 1.0,
    }

    history_detail_response = await client.get(f"/api/v1/exams/{exam['id']}")
    assert history_detail_response.status_code == 200
    history_exam = history_detail_response.json()["exam"]
    history_item = history_exam["items"][0]
    assert history_item["problem"]["text"] == "Historical original text"
    assert history_item["problem"]["correctAnswer"]["display"] == "9"
    assert history_item["grading"]["status"] == "correct"

    update_response = await client.patch(
        f"/api/v1/problems/{problem['id']}",
        json={
            "text": "Historical edited text",
            "correctAnswer": "10",
            "tags": ["changed"],
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["problem"]["text"] == "Historical edited text"

    immutable_history_response = await client.get(f"/api/v1/exams/{exam['id']}")
    assert immutable_history_response.status_code == 200
    immutable_item = immutable_history_response.json()["exam"]["items"][0]
    assert immutable_item["problem"]["text"] == "Historical original text"
    assert immutable_item["problem"]["correctAnswer"]["display"] == "9"
