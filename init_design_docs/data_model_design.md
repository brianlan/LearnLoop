# LearnLoop Data Model Design

## 1. Purpose

This document defines the logical and persistence data model for LearnLoop. It maps the conceptual entities in `REQUIREMENTS.md` to concrete records, embedded structures, lifecycle states, and indexing strategy.

## 2. Data Modeling Principles

1. **User isolation is mandatory**: every domain record is scoped to exactly one user.
2. **Current state and history are separate concerns**: mutable problems and immutable submitted exams must not share storage semantics.
3. **Uploads before confirmation need temporary persistence**: ingestion previews are explicit resources.
4. **Current problem tracking is summary state**: historical per-exam details live inside exam records.
5. **Object store references are metadata only**: images live outside MongoDB.

## 3. Collections Overview

The MVP should use these MongoDB collections:

- `users`
- `sessions`
- `ingestion_previews`
- `problems`
- `exams`

## 4. Collection Designs

## 4.1 `users`

Represents authenticated students.

### Fields

```json
{
  "_id": "ObjectId",
  "username": "string",
  "passwordHash": "string",
  "createdAt": "datetime",
  "updatedAt": "datetime",
  "lastLoginAt": "datetime|null",
  "status": "active"
}
```

### Notes

- `username` is the public unique identity within the app
- plaintext passwords are never stored
- `status` is fixed to `active` for MVP, but keeps room for future account lifecycle changes

### Indexes

- unique index on `username`

## 4.2 `sessions`

Represents authenticated browser sessions.

### Fields

```json
{
  "_id": "string",
  "userId": "ObjectId",
  "createdAt": "datetime",
  "expiresAt": "datetime",
  "lastSeenAt": "datetime",
  "invalidatedAt": "datetime|null",
  "clientMeta": {
    "ip": "string|null",
    "userAgent": "string|null"
  }
}
```

### Notes

- `_id` is the opaque session token identifier
- session cookie points to this record
- explicit logout sets `invalidatedAt`
- MVP timeout policy is 24 hours with sliding renewal; authenticated requests may move `expiresAt` forward

### Indexes

- index on `userId`
- index on `expiresAt`

## 4.3 `ingestion_previews`

Represents temporary upload/extraction state before a final problem is confirmed.

### Why This Exists

This collection is required by the workflow implied by:

- upload before confirmation
- VLM retry without losing uploaded image
- edit-before-save behavior
- failure recovery when extraction is empty or unavailable
- browser reload/crash without losing corrected draft edits

### Fields

```json
{
  "_id": "ObjectId",
  "userId": "ObjectId",
  "status": "uploaded|extracting|ready|vlm-failed|confirmed|expired",
  "sourceImage": {
    "bucket": "string",
    "objectKey": "string",
    "contentType": "string",
    "sizeBytes": "number",
    "sha256": "string|null",
    "uploadedAt": "datetime"
  },
  "extraction": {
    "requestModel": "string|null",
    "requestStartedAt": "datetime|null",
    "requestFinishedAt": "datetime|null",
    "success": "boolean|null",
    "rawText": "string|null",
    "rawProblemType": "single-choice|multi-choice|fill-in-the-blank|short-answer|null",
    "rawGraphDsl": "string|null",
    "rawProviderResponse": "object|null",
    "failureCode": "string|null",
    "failureMessage": "string|null"
  },
  "editableDraft": {
    "text": "string|null",
    "problemType": "single-choice|multi-choice|fill-in-the-blank|short-answer|null",
    "graphDsl": "string|null",
    "correctAnswer": "string|null",
    "tags": ["string"]
  },
  "createdAt": "datetime",
  "updatedAt": "datetime",
  "expiresAt": "datetime"
}
```

### Notes

- `editableDraft` is the server copy of the current preview state
- once a problem is confirmed, preview status becomes `confirmed`
- expired previews may be removed automatically with a TTL index
- `extracting` is a valid externally visible state only when the initial extraction did not finish within the bounded synchronous wait window and the browser continues by polling `GET /ingestion-previews/{id}`
- preview TTL is 24 hours from creation or last draft update, whichever is later

### Indexes

- index on `userId, status`
- TTL index on `expiresAt`

## 4.4 `problems`

Represents the current editable study problem owned by a user.

### Fields

```json
{
  "_id": "ObjectId",
  "userId": "ObjectId",
  "text": "string",
  "problemType": "single-choice|multi-choice|fill-in-the-blank|short-answer",
  "graphDsl": "string|null",
  "correctAnswer": {
    "display": "string",
    "normalizedText": "string",
    "normalizedSet": ["string"],
    "format": "single|set"
  },
  "tags": ["string"],
  "sourceImage": {
    "bucket": "string",
    "objectKey": "string",
    "contentType": "string",
    "sizeBytes": "number",
    "sha256": "string|null"
  },
  "origin": {
    "previewId": "ObjectId|null",
    "vlmModel": "string|null",
    "rawExtractedText": "string|null",
    "rawExtractedProblemType": "string|null",
    "rawExtractedGraphDsl": "string|null"
  },
  "tracking": {
    "exposureCount": "number",
    "correctCount": "number",
    "failedCount": "number",
    "lastTestedAt": "datetime|null",
    "lastAttemptCorrect": "boolean|null"
  },
  "isDeleted": "boolean",
  "deletedAt": "datetime|null",
  "createdAt": "datetime",
  "updatedAt": "datetime"
}
```

### Notes

- this is the mutable "latest problem" state
- `correctAnswer` uses canonicalized fields so objective grading stays deterministic
- `normalizedSet` is used for multi-choice answers where ordering should not matter
- multi-choice authoring input is represented as a comma-separated string at the API boundary and canonicalized into a sorted unique token set for persistence
- `tracking` is summary state for selection and per-problem views
- soft delete is modeled by `isDeleted`
- `origin.previewId` is provenance metadata only and may later reference an expired/deleted preview record after TTL cleanup

### Indexes

- index on `userId, isDeleted, updatedAt`
- index on `userId, isDeleted, problemType`
- multikey index on `userId, isDeleted, tags`
- optional compound index on `userId, isDeleted, tracking.lastTestedAt`

## 4.5 `exams`

Represents exam lifecycle and immutable exam history.

### Fields

```json
{
  "_id": "ObjectId",
  "userId": "ObjectId",
  "state": "in-progress|submitted",
  "configSnapshot": {
    "maxProblemCount": "number",
    "selectionPolicy": {
      "recencyWeight": "number",
      "failureWeight": "number"
    },
    "generatedAt": "datetime"
  },
  "items": [
    {
      "itemId": "string",
      "order": "number",
      "problemId": "ObjectId",
      "problemSnapshot": {
        "text": "string",
        "problemType": "single-choice|multi-choice|fill-in-the-blank|short-answer",
        "graphDsl": "string|null",
        "correctAnswer": {
          "display": "string",
          "normalizedText": "string",
          "normalizedSet": ["string"],
          "format": "single|set"
        },
        "sourceImage": {
          "bucket": "string",
          "objectKey": "string"
        }
      },
      "answer": {
        "raw": "string|null",
        "savedAt": "datetime|null"
      },
      "grading": {
        "status": "ungraded|correct|incorrect|pending-review",
        "method": "normalized-match|vlm|self-report|null",
        "isCorrect": "boolean|null",
        "score": "number|null",
        "feedback": "string|null",
        "providerModel": "string|null",
        "rawProviderResponse": "object|null",
        "gradedAt": "datetime|null",
        "retryCount": "number",
        "selfReportedCorrect": "boolean|null"
      }
    }
  ],
  "summary": {
    "totalProblems": "number",
    "answeredProblems": "number",
    "gradedProblems": "number",
    "pendingProblems": "number",
    "correctProblems": "number",
    "failedProblems": "number",
    "score": "number"
  },
  "createdAt": "datetime",
  "startedAt": "datetime|null",
  "submittedAt": "datetime|null",
  "updatedAt": "datetime"
}
```

### Notes

- `items.problemSnapshot` is the authoritative historical view for the exam
- submitted exams are immutable except for explicitly allowed fallback completion fields during the same submission workflow
- unanswered submitted items count as failed per requirements
- short-answer grading must include access to the original image and the stored answer snapshot for consistency
- `pending-review` items are submitted but unresolved; they do not increment correct or failed counters until self-report resolves them
- `createdAt` records exam generation time, while `startedAt` records the first time the user opens the exam for taking/resume
- `score` is a ratio in the range `0.0..1.0`, computed from resolved graded outcomes only

Summary field formulas:

- `totalProblems` = total count of exam items
- `answeredProblems` = count of items with a saved user answer
- `pendingProblems` = count of items with grading status `pending-review`
- `failedProblems` = count of items with grading status `incorrect` (including unanswered items auto-marked incorrect on submission)
- `correctProblems` = count of items with grading status `correct`
- `gradedProblems` = `correctProblems + failedProblems`
- invariant: `gradedProblems + pendingProblems = totalProblems`
- `score` = `correctProblems / gradedProblems` when `gradedProblems > 0`, otherwise `null`

Self-report resolution rule:

- when a `pending-review` item is resolved by self-report, `pendingProblems` decreases by 1, either `correctProblems` or `failedProblems` increases by 1, `gradedProblems` increases by 1, and `score` is recalculated

### Indexes

- index on `userId, state, updatedAt`
- index on `userId, submittedAt`
- partial unique index on `userId` where `state = in-progress`

## 5. Enumerations

## 5.1 Problem Type

- `single-choice`
- `multi-choice`
- `fill-in-the-blank`
- `short-answer`

## 5.2 Exam State

- `in-progress`
- `submitted`

## 5.3 Ingestion Preview State

- `uploaded`
- `extracting`
- `ready`
- `vlm-failed`
- `confirmed`
- `expired`

## 5.4 Grading Status

- `ungraded`
- `correct`
- `incorrect`
- `pending-review`

## 6. Data Relationships

- one `user` owns many `sessions`
- one `user` owns many `ingestion_previews`
- one `user` owns many `problems`
- one `user` owns many `exams`
- one `exam` contains many `items`
- one `problem` may appear in many exam items over time

No cross-user shared ownership exists in the MVP.

## 7. Historical Integrity Rules

1. A submitted exam keeps its own problem snapshot forever.
2. A problem may be updated after exams exist, but updates affect only future exams.
3. A problem may be soft-deleted after exams exist, but exam history remains readable.
4. The original image object key used for a problem must remain stable for historical grading/audit needs.

## 8. Canonical Answer Design

For deterministic objective grading:

- `single-choice` and `fill-in-the-blank` use `normalizedText`
- `multi-choice` uses `normalizedSet` with sorted, unique canonical tokens
- `short-answer` keeps the stored answer snapshot for VLM grading context and fallback review

This avoids ambiguous grading behavior caused by raw free-form strings alone.

Chosen normalization function for objective answers:

1. convert fullwidth ASCII-style characters to standard ASCII where applicable
2. convert to lowercase
3. trim leading and trailing whitespace
4. collapse internal whitespace runs to a single space
5. preserve mathematical punctuation/operators needed for meaning: `+`, `-`, `=`, `*`, `/`, `×`, `÷`, `(`, `)`, `.`
6. remove remaining punctuation characters not in the preserved set

For multi-choice values, the normalized input is additionally split on commas, trimmed per token, deduplicated, sorted, and stored as `normalizedSet`.

Canonicalization by problem type:

| Problem Type | Input Form | Stored `format` | `display` | `normalizedText` | `normalizedSet` |
|---|---|---|---|---|---|
| `single-choice` | single option identifier, e.g. `B` | `single` | original input | normalized input | empty array |
| `multi-choice` | comma-separated option identifiers, e.g. `A,C,D` | `set` | original input | normalized joined representation | canonical sorted unique token list |
| `fill-in-the-blank` | free text | `single` | original input | normalized input | empty array |
| `short-answer` | free text answer key | `single` | original input | normalized input for audit/reference only | empty array |

For `multi-choice`, tokens are option identifiers rather than arbitrary free-text fragments.

## 9. Logging and Audit Data

Observability requirements do not require a dedicated MongoDB audit collection in the MVP.

Recommended approach:

- emit structured application logs for auth events, VLM calls, and exam lifecycle transitions
- include correlation identifiers referencing `userId`, `previewId`, `problemId`, or `examId` where applicable

If future retention/audit needs expand, these logs can be persisted separately without changing core domain collections.

## 10. Data Retention Notes

- `sessions` expire naturally
- `ingestion_previews` are temporary and should expire automatically
- `problems` are soft-deleted, not removed immediately
- `exams` are durable historical records and should not be deleted in normal MVP flows

## 11. Open Design Decisions Resolved by This Model

1. Tracking is stored as summary data on the current `problem` record.
2. Exam history is owned by `exams`, not reconstructed from current problems.
3. Temporary ingestion state is modeled explicitly rather than hidden in client state.
4. One active exam per user is enforced by a partial unique index plus application checks.
