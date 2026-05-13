# LearnLoop Workflow Design

## 1. Purpose

This document defines the end-to-end business workflows for LearnLoop. It translates the requirements into concrete step sequences, state transitions, and failure handling behavior.

## 2. Workflow Inventory

- WF-AUTH-1: Register
- WF-AUTH-2: Login
- WF-AUTH-3: Logout
- WF-ING-1: Problem ingestion from clipboard
- WF-ING-2: Retry failed extraction
- WF-PROBLEM-1: List and inspect problems
- WF-PROBLEM-2: Edit problem
- WF-PROBLEM-3: Soft delete problem
- WF-EXAM-1: Generate exam
- WF-EXAM-2: Take and resume exam
- WF-EXAM-3: Submit exam and grade
- WF-EXAM-4: Resolve pending-review short-answer item
- WF-HISTORY-1: Review problem tracking and exam history

## 3. Authentication Workflows

## 3.1 WF-AUTH-1 Register

1. User submits username and password.
2. System validates required fields and username uniqueness.
3. System hashes password.
4. System creates user record.
5. System logs registration event.
6. System returns success response.

Important rule:

- registration does not automatically log the user in; login remains a separate explicit workflow

Failure cases:

- duplicate username → reject with validation/conflict error
- invalid payload → reject with validation error

## 3.2 WF-AUTH-2 Login

1. User submits username and password.
2. System loads user by username.
3. System verifies password against stored hash.
4. System creates session record.
5. System sets session cookie.
6. System logs login success or failure.
7. System returns authenticated user summary.

Failure cases:

- username not found or password invalid → reject without revealing which field was wrong

## 3.3 WF-AUTH-3 Logout

1. User requests logout.
2. System invalidates session.
3. System clears cookie.
4. System logs logout event.
5. System returns success.

## 4. Ingestion Workflows

## 4.1 WF-ING-1 Problem Ingestion From Clipboard

1. User pastes clipboard content into the ingestion UI.
2. Browser validates that clipboard content is an image.
3. Browser uploads image to backend.
4. Backend stores original image in object storage.
5. Backend creates `ingestion_preview` record in `uploaded/extracting` state.
6. Backend sends image to VLM extraction flow.
7. VLM returns extracted text, detected type, and optional graph DSL.
8. Backend validates response shape and stores raw extraction result.
9. Backend copies extraction output into editable preview draft.
10. Backend waits synchronously for up to 25 seconds.
11. If extraction completes in time, backend returns `ready` or `vlm-failed` preview payload to browser.
12. If extraction does not complete in time, backend returns `extracting` and the browser shows a progress indicator while polling `GET /ingestion-previews/{id}`.
13. Browser renders preview text, problem type, graph preview, correct-answer field, and tags input once preview status becomes `ready`.
14. User edits/corrects fields as needed.
15. Browser auto-saves edits to the preview draft.
16. User confirms save.
17. Backend validates final fields.
18. Backend creates final `problem` record.
19. Backend marks preview as `confirmed`.
20. Backend returns created problem.

Failure cases:

- non-image paste → reject before upload with informative error
- upload failure → return upload error; no preview created
- VLM returns empty or garbled text → still return preview with editable raw result if possible
- VLM returns invalid graph DSL → preview shows graph error state, user may edit/clear DSL
- VLM unavailable → preview survives in `vlm-failed` state and image is preserved
- application restart during long-running `extracting` state → user may need to trigger retry explicitly

## 4.2 WF-ING-2 Retry Failed Extraction

1. User opens a preview in `vlm-failed` state.
2. User requests retry.
3. Backend reuses stored image from object storage.
4. Backend resends extraction request to VLM.
5. Backend waits synchronously for up to 25 seconds.
6. On in-window success, preview becomes `ready`.
7. If the bounded wait window expires, preview remains `extracting` and the browser continues polling.
8. On failure, preview becomes `vlm-failed` with updated failure details.

## 5. Problem Management Workflows

## 5.1 WF-PROBLEM-1 List and Inspect Problems

1. Authenticated user opens problem list.
2. Browser requests problem list with optional filters.
3. Backend queries only the authenticated user's non-deleted problems.
4. Backend returns filtered list with tracking summary.
5. User selects a problem for detail view.
6. Backend returns full problem details and secure image access.

## 5.2 WF-PROBLEM-2 Edit Problem

1. User opens problem detail.
2. User edits any mutable field.
3. Browser submits patch request.
4. Backend validates payload and ownership.
5. Backend updates the current problem record.
6. Backend returns updated problem.

Important rule:

- submitted exam history is unchanged by later edits

## 5.3 WF-PROBLEM-3 Soft Delete Problem

1. User requests deletion.
2. Backend validates ownership.
3. Backend sets `isDeleted = true` and `deletedAt`.
4. Backend excludes the problem from future list views and exam selection.
5. Existing exam history remains available.

## 6. Exam Workflows

## 6.1 WF-EXAM-1 Generate Exam

1. User requests a new exam with `maxProblemCount`.
2. Backend checks for an existing `in-progress` exam for that user.
3. If one exists, reject and direct user to resume or submit it.
4. Backend loads eligible problems:
   - owned by the user
   - not soft-deleted
   - have a stored correct answer
5. Backend computes selection weights using:
   - inverse recency of last exposure
   - failure rate / failure count signal
6. Backend selects ordered problems up to requested maximum.
7. Backend creates exam record with:
   - `in-progress` state
   - ordered exam items
   - problem snapshots
   - config snapshot
8. Backend logs exam creation event.
9. Backend returns the exam.

Failure cases:

- active exam exists → reject
- zero eligible problems → reject with explicit message

## 6.2 WF-EXAM-2 Take and Resume Exam

1. User opens active exam.
2. Backend returns current exam items and saved answers.
3. If this is the first open for the exam, backend sets `startedAt`.
4. User answers items sequentially or via navigation.
5. Each answer submission is persisted immediately.
6. If browser closes or crashes, saved answers remain.
7. When user returns, backend returns the same `in-progress` exam.

## 6.3 WF-EXAM-3 Submit Exam and Grade

1. User submits the active exam.
2. Backend loads the `in-progress` exam.
3. For each item:
    - if unanswered, mark failed
    - if objective type, apply normalized deterministic grading
    - if short-answer, call VLM with original image + user answer + stored answer snapshot
4. If short-answer VLM grading fails on a retryable error, retry once.
5. If it fails again, mark item `pending-review`.
6. Backend computes aggregate exam score.
7. Backend updates each referenced problem's tracking summary for resolved items only.
8. Backend transitions exam state to `submitted`.
9. Backend stores grading metadata and timestamps.
10. Backend logs exam submission event.
11. Backend returns graded exam result.

Consistency rule:

- steps 2 through 9 should occur within one transaction-capable submission boundary
- `pending-review` items are submitted but unresolved, so they do not count as failed until self-report resolves them
- summary calculations use: `gradedProblems = correctProblems + failedProblems`, `pendingProblems = pending-review items`, and `score = correctProblems / gradedProblems` when `gradedProblems > 0`

## 6.4 WF-EXAM-4 Resolve Pending Review Short-Answer Item

1. User sees one or more exam items marked `pending-review`.
2. User self-reports correctness for each pending item.
3. Backend updates item grading method to `self-report`.
4. Backend updates exam summary if needed.
5. Backend ensures problem tracking reflects final resolved outcome.

Resolution rule:

- resolving a pending item decreases `pendingProblems` by 1, increases either `correctProblems` or `failedProblems` by 1, increases `gradedProblems` by 1, and recalculates `score`

## 7. History and Tracking Workflows

## 7.1 WF-HISTORY-1 Review Problem Tracking and Exam History

1. User opens a problem detail or tracking view.
2. Backend returns current tracking summary from the problem record.
3. User opens exam history.
4. Backend returns submitted exam list.
5. User opens one historical exam.
6. Backend returns stored exam item snapshots and grading results.

## 8. Workflow State Models

## 8.1 Ingestion Preview State Model

```text
uploaded -> extracting -> ready -> confirmed
                    \-> vlm-failed -> retry -> extracting
uploaded/ready/vlm-failed -> expired
```

## 8.2 Exam State Model

```text
in-progress -> submitted
```

There is no second active state in the MVP. Resume is reopening the same `in-progress` exam.

## 8.3 Problem Visibility State Model

```text
active -> soft-deleted
```

Soft-deleted problems remain historically referenced but are excluded from active listing and selection.

## 9. Performance-Oriented Workflow Notes

- upload occurs before VLM extraction so images are not lost on VLM failure
- ingestion uses bounded synchronous wait first, then polling if extraction runs long
- exam selection reads summary tracking fields instead of replaying history
- problem lists query indexed current problem records only
- answer persistence is incremental to reduce recovery loss on crash
- submitted exam scores may be temporarily partial when `pending-review` items exist and become final once all pending items are resolved

## 10. Security-Oriented Workflow Notes

- every workflow except register/login/logout requires an authenticated session
- every resource access verifies owner = authenticated user
- browser never sees server-side VLM credentials
- graph rendering happens in sandbox, not in page-global context

## 11. Workflow Decisions to Freeze

1. Upload and confirmation are separate steps connected by an ingestion preview resource.
2. Exam creation snapshots problem content at generation time.
3. Exam answer persistence is per-item, not only at final submit.
4. Exam submission performs grading and tracking update as one consistency boundary.
5. Self-report exists only as fallback for short-answer grading failure.
