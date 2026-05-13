# LearnLoop — Requirements Specification

> **Version:** 1.0 (Initial / MVP)
> **Date:** 2026-05-13
> **Audience:** Solo developer (implementation reference)
> **Abstraction level:** Behavior + externally observable outcomes

---

## 1. Outcomes (Why)

### 1.1 Problem Statement

Pupils and high school students studying math and science need a structured way to build a personal problem bank from textbook screenshots, practice those problems at spaced intervals, and track their mastery over time. Existing tools either lack image-based ingestion, don't support geometric graphs, or provide no spaced-review mechanism.

### 1.2 Objectives

| # | Objective | Success Metric |
|---|-----------|---------------|
| O1 | Enable image-to-problem ingestion with minimal manual entry | A user can go from clipboard paste to a confirmed problem entry in under 2 minutes |
| O2 | Support geometric problems with interactive graph rendering | JSXGraph-based graphs render correctly for at least 80% of graph-containing images |
| O3 | Provide spaced-repetition-driven exam generation | Exams are generated weighted toward weak and not-recently-tested problems |
| O4 | Track mastery at per-problem and per-exam granularity | Every exam answer updates problem-level counters and produces an exam-level score |
| O5 | Extensible architecture for future features | Clean domain boundaries allow adding gamification, sharing, or classroom features without restructuring |

### 1.3 Stakeholders & Target Users

| Role | Description |
|------|-------------|
| Student (primary user) | Pupil or high school student who ingests problems, takes exams, and reviews performance |
| Developer | Solo developer building, maintaining, and extending the system |

---

## 2. Capabilities (What)

### 2.1 Primary Workflows

#### WF-1: Problem Ingestion

```
Clipboard paste (image) → Store original image in object storage →
Send image to VLM → Receive: problem text, JSXGraph DSL (if graph detected),
problem type classification → Display preview (rendered graph + extracted text +
detected type + editable fields) → User edits/corrects text, graph DSL,
type, and enters correct answer + tags → User confirms → Persist problem record
```

#### WF-2: Problem Management

```
User browses/lists their problems (filterable by tags, type) →
Create / Read / Update / Delete problems →
Soft delete preserves exam history
```

#### WF-3: Exam Generation & Taking

```
User requests exam (with configurable max problem count) →
System selects problems using spaced-repetition policy (weight by recency
and failure rate) → Exam paper created with config snapshot →
User answers problems sequentially, progress resumable →
User submits exam → System auto-grades:
  - Single/multi-choice & fill-in: normalized string match
  - Short-answer: VLM-assisted grading (image + user answer → VLM judgment)
→ Tracking data updated → Exam score presented
```

#### WF-4: User Authentication

```
Register (username + password) → Password stored encrypted →
Login → Session established → All data scoped to authenticated user
```

### 2.2 System Responsibilities & Boundaries

**Responsibilities (in scope):**
- Image storage and retrieval
- VLM orchestration (extraction and grading)
- Problem CRUD with user isolation
- Exam lifecycle management
- Spaced-repetition-based problem selection
- Auto-grading (normalized match + VLM-assisted)
- Per-problem and per-exam tracking
- User authentication with encrypted credentials

**Boundaries (not responsible for):**
- Multi-user collaboration or classroom management
- Gamification (leaderboards, badges, streaks)
- Social features (problem or exam sharing)
- Bulk import/export of problem banks
- Native mobile applications

### 2.3 Domain Entities (Conceptual)

| Entity | Definition |
|--------|-----------|
| **User** | A registered student. Identified by username. Owns all their problems, exams, and tracking data. |
| **Problem** | A study question extracted from an image. Contains: extracted text, problem type (single-choice / multi-choice / fill-in-the-blank / short-answer), optional JSXGraph DSL, correct answer, tags, reference to original image in object storage, and a soft-delete flag. Belongs to one User. |
| **Tag** | A user-defined label attached to problems for categorization. Examples: "algebra", "geometry", "chapter-3". |
| **Exam** | A collection of problems selected by the system for the user to answer. Contains: lifecycle state (in-progress / submitted), the ordered list of problems presented, the user's answers, grading results, an aggregate score, timestamps, and a snapshot of the generation configuration used. Belongs to one User. |
| **Problem Tracking** | Per-problem performance counters: number of times exposed in exams, number of correct answers, number of failed answers, timestamp of last test, and whether the last attempt was correct. Scoped to a User + Problem pair. |
| **Exam Tracking** | Per-exam aggregate state: start time, submit time, completion status, total score, and the generation-rule snapshot used. |

### 2.4 Assumptions & Constraints

| ID | Assumption / Constraint |
|----|------------------------|
| AC-1 | Object storage is S3-compatible (RustFS) and available via Docker on localhost |
| AC-2 | Database is MongoDB 4.4.23, running in a local Docker container |
| AC-3 | VLM is accessible via a configurable HTTP endpoint with configurable model name and API key |
| AC-4 | The VLM endpoint follows an OpenAI-compatible chat completions protocol (or equivalent) supporting image inputs |
| AC-5 | JSXGraph DSL generated by the VLM is JavaScript code using the JSXGraph browser library API |
| AC-6 | Users access the application via a web browser (desktop and mobile-responsive) |
| AC-7 | The system serves a single user at a time per account; no concurrent sessions are required to be handled |
| AC-8 | Only one in-progress exam may exist per user at any given time |

---

## 3. Requirements (Shall)

### 3.1 Functional Requirements

#### Authentication & User Management

- **FR-AUTH-01**: The system shall allow a new user to register with a username and password.
- **FR-AUTH-02**: The system shall store passwords in an encrypted (hashed) form; plaintext passwords shall never be persisted.
- **FR-AUTH-03**: The system shall authenticate a user by verifying the submitted password against the stored encrypted credential.
- **FR-AUTH-04**: The system shall establish a session upon successful authentication and require a valid session for all subsequent operations.
- **FR-AUTH-05**: The system shall terminate a user's session upon explicit logout.

#### Problem Ingestion

- **FR-ING-01**: The system shall accept an image pasted from the user's clipboard.
- **FR-ING-02**: The system shall store the original image in S3-compatible object storage and retain a reference (URI) to the stored object.
- **FR-ING-03**: The system shall send the image to the configured VLM and request extraction of: (A) the problem's plain text, (B) a JSXGraph code snippet if a graph is detected in the image, (C) the problem type classification.
- **FR-ING-04**: The system shall classify the problem as exactly one of: single-choice, multi-choice, fill-in-the-blank, or short-answer.
- **FR-ING-05**: The system shall present the user with a preview of the extracted text, a rendered JSXGraph (if DSL was generated), the detected problem type, and editable fields for all three — plus an input field for the correct answer and tags.
- **FR-ING-06**: The system shall allow the user to modify the extracted text, JSXGraph DSL, problem type, and correct answer before confirming.
- **FR-ING-07**: The system shall render the JSXGraph DSL in the preview so the user can visually verify graph correctness.
- **FR-ING-08**: The system shall allow the user to attach one or more tags to the problem during ingestion.
- **FR-ING-09**: Upon user confirmation, the system shall persist the problem record containing: the final text, JSXGraph DSL (or null if none), problem type, correct answer, tags, and the object-storage reference to the original image.

#### Problem Management (CRUD)

- **FR-CRUD-01**: The system shall allow an authenticated user to list all their non-deleted problems, with the ability to filter by tags and problem type.
- **FR-CRUD-02**: The system shall allow an authenticated user to view the full details of any of their problems (text, rendered graph, type, answer, tags, original image).
- **FR-CRUD-03**: The system shall allow an authenticated user to edit any field of their problem (text, graph DSL, type, correct answer, tags).
- **FR-CRUD-04**: The system shall allow an authenticated user to soft-delete a problem. Soft-deleted problems shall be excluded from listing and exam selection, but their tracking data and exam history references shall be preserved.
- **FR-CRUD-05**: The system shall enforce that each user can only access and modify their own problems.

#### Exam Generation

- **FR-EXAM-01**: The system shall generate an exam by selecting problems from the user's non-deleted, non-archived problem set according to a spaced-repetition policy that weights problems by: (A) recency of last exposure (less recently tested → higher priority), and (B) failure rate (higher failure count → higher priority).
- **FR-EXAM-02**: The system shall allow the user to configure the maximum number of problems per exam.
- **FR-EXAM-03**: The system shall reject exam generation if the user already has an exam in the "in-progress" state.
- **FR-EXAM-04**: The system shall create an exam record containing: the selected problems in order, a snapshot of the generation configuration, and a lifecycle state of "in-progress."
- **FR-EXAM-05**: The system shall not select a problem for an exam if no correct answer has been stored for it.

#### Exam Taking & Grading

- **FR-EXAM-06**: The system shall present the exam's problems to the user one at a time (or in a navigable list), displaying the problem text and rendered JSXGraph (if applicable).
- **FR-EXAM-07**: The system shall persist the user's answer for each problem as it is submitted during the exam.
- **FR-EXAM-08**: The system shall allow the user to resume an in-progress exam when they return, preserving all previously entered answers.
- **FR-EXAM-09**: Upon exam submission, the system shall auto-grade each answer:
  - For single-choice, multi-choice, and fill-in-the-blank: compare using normalized string match (case-insensitive, whitespace-trimmed, punctuation-normalized).
  - For short-answer: send the original problem image and the user's answer to the VLM and use the VLM's judgment as the grading result.
- **FR-EXAM-10**: If VLM-assisted grading for a short-answer problem fails, the system shall retry once. If it fails again, the system shall mark that answer as "pending review" and allow the user to self-report correctness.
- **FR-EXAM-11**: The system shall compute and display an aggregate exam score (number correct / total problems).

#### Tracking

- **FR-TRACK-01**: Upon exam submission, the system shall update each included problem's tracking counters: increment exposure count, increment correct or failed count, update last-test-time, and update last-time-is-correct.
- **FR-TRACK-02**: The system shall allow a user to view tracking data for any of their problems.
- **FR-TRACK-03**: The system shall store exam-level tracking data: start time, submission time, completion status (submitted), the ordered list of problems with user answers and grading results, aggregate score, and the generation-rule configuration snapshot.
- **FR-TRACK-04**: The system shall allow a user to view their exam history including scores and per-problem results.

### 3.2 Non-Functional Requirements

#### Performance

- **NFR-PERF-01**: Image upload to object storage shall complete within 10 seconds for images up to 10MB.
- **NFR-PERF-02**: VLM extraction (ingestion) shall provide feedback to the user within 30 seconds; if the VLM takes longer, the system shall display a progress indicator.
- **NFR-PERF-03**: Exam generation shall complete within 2 seconds for problem banks up to 1,000 problems.
- **NFR-PERF-04**: Problem listing shall return results within 1 second for banks up to 1,000 problems.

#### Reliability

- **NFR-REL-01**: The system shall handle VLM unavailability gracefully during ingestion by notifying the user and allowing retry, without losing the uploaded image.
- **NFR-REL-02**: Exam progress (answers entered so far) shall be persisted after each answer so that no data is lost on browser crash or closure.

#### Security

- **NFR-SEC-01**: Passwords shall be hashed using a salted cryptographic hash function (e.g., bcrypt or equivalent).
- **NFR-SEC-02**: User data (problems, exams, tracking) shall be strictly isolated; no user shall be able to access another user's data.
- **NFR-SEC-03**: All VLM API keys and credentials shall be stored server-side only and never exposed to the browser client.
- **NFR-SEC-04**: JSXGraph DSL rendered in the browser shall be sandboxed such that it cannot execute arbitrary JavaScript outside the JSXGraph rendering context.

#### Privacy

- **NFR-PRIV-01**: Problem images and content belong to the user who uploaded them and shall not be accessible to other users.

### 3.3 Data & Domain Definitions

| Concept | Definition |
|---------|-----------|
| **Problem Type** | An enumeration: `single-choice`, `multi-choice`, `fill-in-the-blank`, `short-answer`. |
| **Exam Lifecycle State** | An enumeration: `in-progress`, `submitted`. |
| **Soft Delete** | A logical deletion where the record is marked as deleted and excluded from queries, but retained in storage for historical reference. |
| **Normalized String Match** | A comparison where both strings are: converted to lowercase, stripped of leading/trailing whitespace, and had punctuation normalized before comparison. |
| **Spaced-Repetition Policy** | A selection policy where problems are weighted by inverse recency of last exposure and proportional failure rate. The exact weighting formula is implementation-defined. |
| **Exam Configuration Snapshot** | An immutable record of the generation parameters (max problem count, effective rule weights) captured at exam creation time for reproducibility. |

### 3.4 Observability & Auditability

- **OBS-01**: The system shall log all VLM calls (ingestion and grading) with: timestamp, request type, success/failure, and latency.
- **OBS-02**: The system shall log all authentication events (registration, login success, login failure, logout).
- **OBS-03**: The system shall log all exam lifecycle transitions (created, resumed, submitted).

### 3.5 Operations & Lifecycle

- **OPS-01**: The system shall be deployable via Docker Compose (or equivalent) starting: the application, MongoDB, and RustFS containers.
- **OPS-02**: VLM endpoint, model name, and API key shall be configurable via environment variables.
- **OPS-03**: Object storage endpoint and credentials shall be configurable via environment variables.
- **OPS-04**: MongoDB connection string shall be configurable via environment variables.

### 3.6 Edge Cases & Failure Handling

| Edge Case | System Behavior |
|-----------|----------------|
| VLM returns empty or garbled text | User is shown the raw result and can edit or re-paste the image |
| VLM generates invalid JSXGraph DSL | Preview renders an error state; user can edit or clear the DSL field |
| VLM misclassifies problem type | User can override the type in the confirmation form |
| VLM is unavailable during ingestion | System notifies user; uploaded image is preserved; user may retry |
| VLM fails during short-answer grading | Retry once; on second failure, mark answer as "pending review" and allow self-report |
| User pastes non-image content from clipboard | System rejects the paste and displays an informative message |
| Exam generation requested with 0 eligible problems | System displays an error indicating no problems are available |
| Exam generation requested while an exam is in-progress | System rejects and directs user to resume or submit the active exam |
| User submits exam with unanswered problems | System grades answered problems; unanswered problems count as failed |
| User deletes a problem used in past exams | Problem is soft-deleted; exam history and tracking data are preserved with the problem marked as deleted |

### 3.7 Non-Goals

The following are explicitly out of scope for this initial version:

- Multi-user collaboration, teacher dashboards, or classroom management
- Gamification features (leaderboards, badges, streaks)
- Social features (sharing problems or exams with other users)
- Bulk import/export of problem banks (PDF, Word, CSV)
- Native mobile applications (iOS/Android)
- Offline mode or PWA capabilities
- Multi-factor authentication (MFA) or role-based access control (RBAC)
- Rate limiting on login or API endpoints
- Advanced spaced-repetition algorithms (SM-2, Anki-style) beyond the policy-level definition
- Formal accessibility compliance certification
- CI/CD pipeline setup
- Internationalization / multi-language support

---

## 4. Acceptance Criteria

| ID | Criterion | Test Evidence |
|----|-----------|---------------|
| AC-01 | A user can register, log in, and log out successfully | Manual E2E walkthrough |
| AC-02 | A user can paste an image from clipboard, receive VLM-extracted text/graph/type, edit all fields, enter an answer and tags, and save a confirmed problem | Manual E2E walkthrough with a real screenshot |
| AC-03 | A problem with a graph renders a JSXGraph preview that visually matches the original image | Visual comparison during ingestion |
| AC-04 | A user can list, view, edit, and soft-delete their problems | CRUD operations verified manually |
| AC-05 | A user cannot access or see another user's problems | Attempted cross-user access returns empty/unauthorized |
| AC-06 | A user can generate an exam that selects problems weighted toward less-recently-tested and higher-failure problems | Exam content reflects tracking-weighted selection |
| AC-07 | A user can answer exam problems, resume after browser closure, and submit | Exam state persists across sessions |
| AC-08 | Choice and fill-in problems are auto-graded with normalized string match; grading result is correct | Unit tests with known answer pairs |
| AC-09 | Short-answer problems trigger VLM-assisted grading; on VLM failure, falls back to self-report | Test with VLM mock (success + failure scenarios) |
| AC-10 | After exam submission, per-problem tracking counters and exam-level data are updated and viewable | Tracking data verified post-submission |
| AC-11 | Soft-deleted problems are excluded from listings and exam generation but preserved in exam history | Verify listing exclusion + history preservation |
| AC-12 | The system deploys with a single Docker Compose command and all services start correctly | `docker compose up` succeeds; health checks pass |
| AC-13 | Unit tests exist for grading logic (normalized match) and exam selection policy | Test suite passes |

---

## 5. Decision Log

| # | Decision | Rationale |
|---|----------|-----------|
| D-01 | User types correct answer manually during ingestion | VLM cannot reliably extract answers from problem images; manual input is most accurate |
| D-02 | Normalized string match for choice/fill-in grading | Sufficient accuracy for objective question types; keeps grading simple and deterministic |
| D-03 | VLM-assisted grading for short-answer | Short answers are too variable for string matching; VLM provides reasonable semantic comparison |
| D-04 | Retry-once-then-self-report for VLM grading failure | Balances reliability (retry) with user autonomy (self-report fallback) |
| D-05 | Policy-level spaced repetition (no specific algorithm) | Allows implementation flexibility while enforcing the core intent (recency + failure weighting) |
| D-06 | Single active exam per user | Simplifies UX and state management; avoids confusion from juggling multiple exams |
| D-07 | Soft delete for problems | Preserves exam history integrity; allows potential future recovery |
| D-08 | Basic auth without rate limiting or MFA | Acceptable for MVP / solo-use context; extensible to stronger security later |
| D-09 | Exam progress persisted per-answer | Prevents data loss on browser crash; supports the resumable exam requirement |
| D-10 | Extensibility-first architecture priority | User chose clean domain boundaries over raw speed, enabling future feature additions |

---

## 6. Open Questions

### Blocking
None. All critical decisions have been resolved.

### Non-Blocking

| # | Question | Notes |
|---|----------|-------|
| Q-01 | Should the system support editing the correct answer after a problem has been used in exams? | Currently allowed by FR-CRUD-03; tracking implications not yet specified |
| Q-02 | Should tags be a flat list or support hierarchy (e.g., "math > algebra > quadratic")? | Currently flat; hierarchy can be added later |
| Q-03 | What is the expected maximum problem bank size? | NFRs are written for up to 1,000 problems; may need adjustment |
| Q-04 | Should the system support problem images from file upload (not just clipboard)? | Currently clipboard-only; file upload is a natural extension |

---

## 7. Traceability

| Requirement | Objective(s) | Workflow |
|-------------|-------------|----------|
| FR-AUTH-01..05 | O5 | WF-4 |
| FR-ING-01..09 | O1, O2 | WF-1 |
| FR-CRUD-01..05 | O5 | WF-2 |
| FR-EXAM-01..05 | O3, O5 | WF-3 |
| FR-EXAM-06..11 | O3, O4 | WF-3 |
| FR-TRACK-01..04 | O4 | WF-3 |
| NFR-PERF-01..04 | O1, O3 | All |
| NFR-REL-01..02 | O4 | WF-1, WF-3 |
| NFR-SEC-01..04 | O5 | All |
| OBS-01..03 | O4 | All |
| OPS-01..04 | O5 | Deployment |
