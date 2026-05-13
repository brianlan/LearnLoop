# LearnLoop API Design

## 1. Purpose

This document defines the external HTTP API shape for LearnLoop. The API is designed for a browser client using cookie-based sessions. All endpoints are user-scoped and all server-side integrations with VLM and object storage remain behind these APIs.

## 2. API Principles

1. **Cookie-authenticated API**: browser sends session cookie automatically.
2. **User-scoped by default**: no endpoint accepts a user identifier from the client for access control.
3. **Preview-before-persist for ingestion**: image upload and final problem creation are separate operations.
4. **Incremental exam persistence**: answers are saved per item during the exam.
5. **Historical immutability**: exam history endpoints return stored snapshots, not reconstructed current problem state.
6. **Bounded synchronous extraction**: ingestion first attempts synchronous extraction, then falls back to polling if extraction exceeds the bounded wait window.

## 3. Base Conventions

- Base path: `/api/v1`
- Content type: `application/json` except upload endpoints
- Authentication: HttpOnly cookie carrying opaque session ID
- Time format: ISO 8601 UTC strings
- Identifier exposure: API responses may expose string IDs while persistence uses MongoDB `ObjectId`; the external ID format is an API presentation concern and does not change ownership or lookup rules
- Default pagination: `page=1`, `pageSize=20`
- Maximum pagination size: `pageSize=100`

## 4. Error Model

All error responses should follow this shape:

```json
{
  "error": {
    "code": "string",
    "message": "string",
    "details": {}
  }
}
```

### Standard Error Codes

- `UNAUTHENTICATED`
- `FORBIDDEN`
- `NOT_FOUND`
- `VALIDATION_ERROR`
- `CONFLICT`
- `VLM_UNAVAILABLE`
- `GRAPH_RENDER_INVALID`
- `NO_ELIGIBLE_PROBLEMS`
- `ACTIVE_EXAM_EXISTS`
- `EXAM_NOT_IN_PROGRESS`

## 5. Authentication API

## 5.1 Register

`POST /api/v1/auth/register`

### Request

```json
{
  "username": "student1",
  "password": "secret"
}
```

### Response

```json
{
  "user": {
    "id": "usr_123",
    "username": "student1"
  }
}
```

Notes:

- registration does **not** automatically create a session in the MVP; login is a separate explicit action

## 5.2 Login

`POST /api/v1/auth/login`

### Request

```json
{
  "username": "student1",
  "password": "secret"
}
```

### Response

```json
{
  "user": {
    "id": "usr_123",
    "username": "student1"
  }
}
```

Behavior:

- server verifies password hash
- server sets session cookie
- server logs auth success/failure event

## 5.3 Logout

`POST /api/v1/auth/logout`

### Response

```json
{
  "ok": true
}
```

## 5.4 Current Session

`GET /api/v1/auth/me`

### Response

```json
{
  "authenticated": true,
  "user": {
    "id": "usr_123",
    "username": "student1"
  }
}
```

## 6. Ingestion Preview API

## 6.1 Create Preview From Clipboard Image

`POST /api/v1/ingestion-previews`

Content type: `multipart/form-data`

### Request Parts

- `image`: uploaded image blob

### Response

```json
{
  "preview": {
    "id": "prv_123",
    "status": "ready",
    "sourceImage": {
      "contentType": "image/png",
      "sizeBytes": 245123
    },
    "draft": {
      "text": "Extracted problem text",
      "problemType": "short-answer",
      "graphDsl": "...",
      "correctAnswer": null,
      "tags": []
    },
    "extraction": {
      "success": true,
      "failureCode": null,
      "failureMessage": null
    }
  }
}
```

Behavior:

- upload original image to object storage first
- create preview record
- call VLM for extraction
- wait synchronously for up to 25 seconds
- if extraction completes in time, return `ready` or `vlm-failed`
- if extraction exceeds the bounded wait window, return `extracting` with the preview ID and continue via polling

Possible returned statuses:

- `ready`
- `vlm-failed`
- `extracting`

## 6.2 Get Preview

`GET /api/v1/ingestion-previews/{previewId}`

Returns the current preview state for the authenticated owner.

This endpoint is also the polling endpoint when create/retry returns `extracting`.

Behavioral note:

- when the preview is in `extracting`, the server is waiting for an in-flight background extraction attempt to complete; if the application restarted before completion, the client should retry extraction explicitly

## 6.3 Save Preview Draft Edits

`PATCH /api/v1/ingestion-previews/{previewId}/draft`

### Request

```json
{
  "text": "Corrected problem text",
  "problemType": "multi-choice",
  "graphDsl": "corrected graph code or null",
  "correctAnswer": "A,C,D",
  "tags": ["geometry", "chapter-3"]
}
```

### Response

```json
{
  "preview": {
    "id": "prv_123",
    "status": "ready",
    "draft": {
      "text": "Corrected problem text",
      "problemType": "multi-choice",
      "graphDsl": "...",
      "correctAnswer": "A,C,D",
      "tags": ["geometry", "chapter-3"]
    }
  }
}
```

Behavior:

- persists user edits before final confirmation
- supports draft recovery on browser refresh/crash
- extends the preview TTL window

## 6.4 Retry Extraction

`POST /api/v1/ingestion-previews/{previewId}/retry`

### Response

Same shape as `GET` preview.

Behavior:

- reuses the stored image object
- retries VLM extraction without requiring re-upload
- applies the same bounded synchronous wait behavior as preview creation

## 6.5 Confirm Preview as Problem

`POST /api/v1/problems`

### Request

```json
{
  "previewId": "prv_123",
  "text": "Corrected problem text",
  "problemType": "short-answer",
  "graphDsl": "corrected graph code or null",
  "correctAnswer": "student-entered answer key",
  "tags": ["geometry", "chapter-3"]
}
```

### Response

```json
{
  "problem": {
    "id": "prb_123",
    "text": "Corrected problem text",
    "problemType": "short-answer",
    "graphDsl": "...",
    "tags": ["geometry", "chapter-3"],
    "isDeleted": false,
    "tracking": {
      "exposureCount": 0,
      "correctCount": 0,
      "failedCount": 0,
      "lastTestedAt": null,
      "lastAttemptCorrect": null
    }
  }
}
```

## 7. Problem Management API

## 7.1 List Problems

`GET /api/v1/problems?tag=geometry&problemType=short-answer&page=1&pageSize=20`

### Response

```json
{
  "items": [
    {
      "id": "prb_123",
      "text": "...",
      "problemType": "short-answer",
      "tags": ["geometry"],
      "isDeleted": false,
      "tracking": {
        "exposureCount": 2,
        "correctCount": 1,
        "failedCount": 1,
        "lastTestedAt": "2026-05-13T08:00:00Z",
        "lastAttemptCorrect": false
      }
    }
  ],
  "page": 1,
  "pageSize": 20,
  "total": 42
}
```

## 7.1.1 List User Tags

`GET /api/v1/tags`

### Response

```json
{
  "items": ["algebra", "geometry", "chapter-3"]
}
```

Behavior:

- returns distinct tags from the authenticated user's non-deleted problems

## 7.2 Get Problem Detail

`GET /api/v1/problems/{problemId}`

### Response

Returns full problem details including graph DSL and source image access URL or backend media route.

## 7.3 Update Problem

`PATCH /api/v1/problems/{problemId}`

### Request

```json
{
  "text": "Updated text",
  "problemType": "fill-in-the-blank",
  "graphDsl": null,
  "correctAnswer": "42",
  "tags": ["algebra"]
}
```

Behavior:

- updates only current mutable problem state
- does not rewrite any submitted exam history
- for `multi-choice`, the accepted authoring input format is a comma-separated string such as `A,C,D`
- all problem creation in the MVP originates from a confirmed ingestion preview; this endpoint is not intended for independent problem creation outside that flow

## 7.4 Soft Delete Problem

`DELETE /api/v1/problems/{problemId}`

### Response

```json
{
  "ok": true
}
```

Behavior:

- sets `isDeleted = true`
- excludes problem from listings and future exam selection
- preserves historical exam references

## 7.5 Problem Tracking View

`GET /api/v1/problems/{problemId}/tracking`

### Response

```json
{
  "problemId": "prb_123",
  "tracking": {
    "exposureCount": 5,
    "correctCount": 3,
    "failedCount": 2,
    "lastTestedAt": "2026-05-13T08:00:00Z",
    "lastAttemptCorrect": true
  }
}
```

## 8. Exam API

## 8.1 Create Exam

`POST /api/v1/exams`

### Request

```json
{
  "maxProblemCount": 10
}
```

### Response

```json
{
  "exam": {
    "id": "exm_123",
    "state": "in-progress",
    "configSnapshot": {
      "maxProblemCount": 10,
      "selectionPolicy": {
        "recencyWeight": 1,
        "failureWeight": 1
      },
      "generatedAt": "2026-05-13T08:00:00Z"
    },
    "items": [
      {
        "itemId": "item_1",
        "order": 1,
        "problem": {
          "text": "...",
          "problemType": "single-choice",
          "graphDsl": null
        },
        "answer": null
      }
    ]
  }
}
```

Errors:

- `ACTIVE_EXAM_EXISTS`
- `NO_ELIGIBLE_PROBLEMS`

## 8.2 Get Active Exam

`GET /api/v1/exams/active`

Returns the current in-progress exam, if any.

## 8.3 Get Exam Detail

`GET /api/v1/exams/{examId}`

Returns:

- current in-progress exam state for resume
- submitted exam history view with per-item results

## 8.4 Save Answer for Exam Item

`PUT /api/v1/exams/{examId}/items/{itemId}/answer`

### Request

```json
{
  "answer": "B"
}
```

### Response

```json
{
  "itemId": "item_1",
  "saved": true,
  "savedAt": "2026-05-13T08:10:00Z"
}
```

Behavior:

- allowed only for `in-progress` exams
- persists after every answer submission

## 8.5 Submit Exam

`POST /api/v1/exams/{examId}/submit`

### Response

```json
{
  "exam": {
    "id": "exm_123",
    "state": "submitted",
    "summary": {
      "totalProblems": 10,
      "answeredProblems": 9,
      "gradedProblems": 9,
      "pendingProblems": 1,
      "correctProblems": 7,
      "failedProblems": 2,
      "score": 0.778
    },
    "items": [
      {
        "itemId": "item_1",
        "correctAnswer": "B",
        "grading": {
          "status": "correct",
          "method": "normalized-match",
          "isCorrect": true,
          "feedback": null
        }
      }
    ]
  }
}
```

Behavior:

- grades objective answers by canonical normalized comparison
- grades short answers through the VLM adapter
- retries VLM grading once on retryable failure
- on second failure marks the item `pending-review`
- excludes `pending-review` items from final correct/failed tracking counters until resolved
- updates per-problem tracking for resolved outcomes only
- transitions exam to `submitted`

Summary semantics:

- `gradedProblems = correctProblems + failedProblems`
- `failedProblems` includes unanswered items auto-marked incorrect during submission
- `score = correctProblems / gradedProblems` when `gradedProblems > 0`, otherwise `null`

## 8.6 Resolve Pending Review by Self-Report

`POST /api/v1/exams/{examId}/items/{itemId}/self-report`

### Request

```json
{
  "isCorrect": true
}
```

### Response

```json
{
  "itemId": "item_1",
  "grading": {
    "status": "correct",
    "method": "self-report",
    "isCorrect": true
  }
}
```

Use only when the item is in `pending-review` state.

Behavior:

- resolves the item into `correct` or `incorrect`
- updates exam summary counts and ratio score
- updates the current problem tracking summary for the resolved item
- is the only allowed path for resolving `pending-review` items after submission

## 8.7 Exam History List

`GET /api/v1/exams?page=1&pageSize=20`

### Response

```json
{
  "items": [
    {
      "id": "exm_123",
      "state": "submitted",
      "createdAt": "2026-05-13T08:00:00Z",
      "submittedAt": "2026-05-13T08:25:00Z",
      "summary": {
        "score": 0.7,
        "totalProblems": 10,
        "correctProblems": 7
      }
    }
  ],
  "page": 1,
  "pageSize": 20,
  "total": 5
}
```

## 9. Media Access API

## 9.1 Get Problem Source Image

`GET /api/v1/problems/{problemId}/image`

Behavior:

- server verifies ownership
- server either streams the image or redirects to a short-lived signed URL

This avoids exposing public permanent object URLs.

## 9.2 Get Exam Item Source Image

`GET /api/v1/exams/{examId}/items/{itemId}/image`

Behavior:

- server verifies ownership via the exam record
- server reads the image reference from the exam item's stored problem snapshot
- server either streams the image or redirects to a short-lived signed URL

This route supports active exams and historical exam review even if the underlying problem has later been edited or soft-deleted.

## 10. VLM Integration Contract Boundary

The browser never calls the VLM directly.

The application service must send structured requests that include:

- request type: `ingestion` or `short-answer-grading`
- image reference or image payload
- model name
- prompt version / schema version

For short-answer grading, the request should include:

- original problem image
- user answer
- stored correct-answer snapshot
- expected response schema

This keeps grading aligned with the manually entered answer key.

Provider contract notes:

- extraction requests should ask for structured fields: `text`, `problemType`, `graphDsl`, and provider metadata
- grading requests should ask for structured fields: `isCorrect`, `feedback`, and provider metadata
- all provider responses must be shape-validated before they influence domain state
- prompt/schema versions should be persisted so extraction and grading outcomes remain auditable against the exact contract in use at the time

## 11. Retry and Timeout Policy

- retry VLM calls only for retryable provider/network failures
- objective grading must not retry because it is local and deterministic
- ingestion retry is exposed as an explicit user action because the uploaded image already exists
- timeouts should be shorter than the user-facing SLA and produce explicit failure codes

## 12. Authorization Rules

Every endpoint that references a preview, problem, exam, or session must verify:

1. request is authenticated
2. resource exists
3. resource `userId` matches session user

No cross-user query paths exist in the API.
