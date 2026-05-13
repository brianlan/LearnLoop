# LearnLoop Architecture Design

## 1. Purpose

This document defines the target system architecture for LearnLoop based on `REQUIREMENTS.md`. It focuses on system structure, trust boundaries, deployment shape, and architectural decisions required to satisfy the MVP requirements.

## 2. Architecture Summary

LearnLoop should be implemented as a **modular web monolith**:

- **One browser-based web client** for student interactions
- **One application service** exposing HTTP APIs and serving the web app
- **One MongoDB database** as the system of record
- **One S3-compatible object store (RustFS)** for original problem images
- **One external VLM endpoint** for extraction and short-answer grading

This is intentionally **not** a microservice architecture. The MVP scope, single-developer ownership, Docker Compose deployment requirement, and modest scale target (up to 1,000 problems per user) favor a single deployable application with clear internal module boundaries.

## 3. Architectural Drivers

The following requirements drive the architecture:

- Clipboard image ingestion with preview/edit before persistence
- Problem CRUD with user isolation and soft delete
- One active exam per user, resumable with per-answer persistence
- Deterministic grading for objective question types
- VLM-assisted grading for short-answer questions with retry/fallback
- Strict separation of server-only credentials from browser code
- Sandboxed rendering for VLM-generated JSXGraph DSL
- Docker Compose deployment with MongoDB and RustFS

## 4. System Context

## 4.1 External Actors

- **Student browser**: uploads images, edits extracted content, manages problems, takes exams
- **VLM provider**: receives image-based extraction/grading requests and returns structured outputs
- **RustFS (S3-compatible)**: stores original images privately
- **MongoDB**: stores users, sessions, previews, problems, exams, and related state

## 4.2 Top-Level Context Diagram

```text
Browser
  |
  v
LearnLoop Web Application
  |-- MongoDB
  |-- RustFS (S3-compatible storage)
  '-- VLM HTTP Endpoint
```

## 5. Chosen Architectural Style

## 5.1 Modular Monolith

The application service should be organized into internal layers:

1. **Presentation Layer**
   - HTTP routes/controllers
   - session/cookie handling
   - request validation
   - response shaping

2. **Application Layer**
   - orchestration of workflows
   - transaction boundaries
   - authorization decisions
   - coordination across domain services and infrastructure adapters

3. **Domain Layer**
   - problem lifecycle rules
   - exam lifecycle rules
   - selection policy
   - grading normalization rules
   - tracking rules

4. **Infrastructure Layer**
   - MongoDB repositories
   - object storage adapter
   - VLM adapter
   - graph sandbox integration support
   - structured logging

## 5.2 Why This Style Fits

- Avoids unnecessary operational complexity
- Keeps transaction-sensitive workflows inside one process boundary
- Supports future extraction into services if scope expands
- Makes local Docker Compose deployment straightforward

## 6. Runtime Components

## 6.1 Browser Client

Responsibilities:

- authenticate via session cookie
- accept clipboard image paste
- upload image blob to backend
- display extracted preview data
- allow user edits before confirmation
- render problems and exams
- persist answer submissions incrementally

Constraints:

- must never hold VLM keys or object-storage credentials
- must never directly call the VLM
- must never render untrusted graph code in the main page context

## 6.2 Application Service

Responsibilities:

- own all business workflows
- enforce user isolation
- enforce one-active-exam rule
- own grading and tracking rules
- issue and invalidate sessions
- broker access to stored images and VLM operations

## 6.3 MongoDB

MongoDB is the persistent system of record for:

- users
- sessions
- ingestion previews
- problems
- exams

Important architectural decision:

- **Exam submission must be modeled as an atomic server-side transition**
- Therefore the deployment should support **MongoDB transactions**
- For MongoDB 4.4 this means the Docker Compose deployment should run Mongo in a transaction-capable replica set configuration
- The MVP deployment should use a **single-node replica set**, not standalone MongoDB, so transactions are available without introducing a multi-node cluster

## 6.4 Object Storage

RustFS stores original uploaded images as immutable blobs.

Design rules:

- store only backend-controlled object keys in domain records
- do not expose public object URLs
- route user access through the application service or signed short-lived URLs created server-side
- treat upload artifacts and confirmed problem ownership as separate states

## 6.5 VLM Adapter

The VLM endpoint is an unreliable external dependency and should be isolated behind a dedicated adapter.

Design rules:

- use server-side only credentials
- use structured request/response contracts
- log request type, success/failure, latency, and model metadata
- retry only retryable failures
- keep raw provider output for audit/debug, but never let raw output become authoritative domain state without validation
- when ingestion falls back to `extracting`, the monolith continues the in-flight extraction in an application-managed background execution path and writes the result back to the preview record on completion
- if the application process stops before that write-back occurs, the preview may remain in `extracting` until the user retries or the preview expires; this is acceptable for MVP

## 7. Trust Boundaries

## 7.1 Browser ↔ Server Boundary

The browser is untrusted for business authority.

- All authorization is server-side
- All user scoping is server-side
- All exam selection and grading are server-side
- All correct answers remain server-authoritative

## 7.2 Server ↔ VLM Boundary

The VLM is useful but non-authoritative.

- extraction output is advisory until user confirms
- short-answer grading output is authoritative only for the current exam item outcome after server validation of response shape
- raw VLM output must be retained as supporting evidence, not as the only stored result

## 7.3 Server ↔ Object Storage Boundary

Object storage is a blob repository, not a business workflow engine.

- object store does not decide access permissions
- application service decides object ownership and access
- uploaded images must remain private to the owning user

## 7.4 Graph Rendering Boundary

This is the highest-risk browser boundary.

The requirements assume the VLM may generate JSXGraph JavaScript-like DSL. That DSL must be treated as untrusted code-like input.

Architectural decision:

- render graph DSL only inside an **isolated sandbox renderer**
- never execute graph DSL in the main browser page context
- use a narrow message-based interface between page and renderer
- apply pre-render validation to reject obviously unsafe or malformed input
- if rendering fails, show an error state and allow user edit/clear

Chosen MVP sandbox mechanism:

- use a dedicated **iframe sandbox** with `sandbox="allow-scripts"`
- do **not** grant `allow-same-origin`, `allow-forms`, or top-navigation permissions
- load JSXGraph only inside the iframe document
- communicate only via strict `postMessage` payloads such as `render`, `clear`, and `error`
- reject DSL containing clearly unsafe tokens before rendering, including patterns like `fetch(`, `XMLHttpRequest`, `eval(`, `import(`, `<script`, `document.cookie`, `window.location`, `localStorage`, and `sessionStorage`
- on timeout or render failure, discard the iframe instance and recreate it rather than trying to recover in-place
- the denylist is **defense-in-depth only**; the iframe sandbox boundary is the primary protection and must not depend on pattern filtering to be safe

This decision satisfies the spirit of `NFR-SEC-04` while preserving the required preview capability.

## 8. State Ownership Decisions

## 8.1 Problem Records

Problem records represent the **current editable version** of a problem.

They own:

- latest text
- latest type
- latest graph DSL
- latest correct answer
- tags
- soft-delete state
- cumulative tracking summary

## 8.2 Exam Records

Exam records represent **immutable historical snapshots** once submitted.

They own:

- selected problem order
- per-item problem snapshot at exam creation time
- user answers
- grading results
- submission timestamps
- exam configuration snapshot
- aggregate score

Critical decision:

- editing or deleting a problem after an exam has been created must not rewrite historical exam content or scores

## 8.3 Ingestion Preview Records

The ingestion preview is a temporary workflow resource between upload and confirmed persistence.

It exists to support:

- VLM retries without losing the uploaded image
- user correction before final problem creation
- graceful failure when extraction is empty/garbled

## 9. Session and Authentication Design

Chosen session model:

- username/password authentication
- password stored as salted cryptographic hash
- server-issued opaque session identifier stored in an HttpOnly cookie
- session document persisted server-side
- 24-hour session expiry with sliding renewal on authenticated requests

Sliding renewal rule:

- each successful authenticated request extends `expiresAt` to 24 hours from the time of that request

Why this model:

- aligns with requirement wording around sessions
- keeps credential logic simple for MVP
- avoids exposing auth state to browser JavaScript
- supports explicit logout by invalidating the session server-side

## 10. Consistency Design

## 10.1 Strong Consistency Areas

The following operations require strong consistency:

- registration with unique username
- login/session issuance
- one-active-exam creation check
- answer save during active exam
- exam submission and tracking update

## 10.2 Atomic Exam Submission

Exam submission should occur as one application-level transaction:

1. load in-progress exam
2. compute per-item grading results
3. compute aggregate score
4. update each referenced problem's tracking counters
5. transition exam state from `in-progress` to `submitted`

If any step fails, the submission transaction should roll back.

Pending-review rule:

- short-answer items that fail VLM grading twice are stored as `pending-review`
- `pending-review` items are **not counted as failed at submission time**
- they are excluded from final correct/failed tracking counters until resolved
- the submitted exam may therefore contain unresolved items after the submission transaction completes
- a later self-report resolution updates both the exam summary and the current problem tracking summary for that item

## 10.3 Failure Tolerance

External failures must not destroy user work.

- upload succeeds but extraction fails → preview survives and can retry
- short-answer VLM grading fails twice → mark as `pending-review` and allow self-report
- browser crash during exam → saved answers remain recoverable

## 11. Deployment Topology

The MVP deployment should use Docker Compose with these containers:

- `app`
- `mongodb`
- `rustfs`

The VLM remains external and is configured by environment variables.

MongoDB deployment note:

- the `mongodb` container must start with replica-set mode enabled and must be initialized as a single-node replica set during environment startup

## 12. Security Design Summary

- server-side only VLM credentials
- private object storage
- user-scoped database access on every query
- hashed passwords only
- server-side session management
- graph sandbox isolation
- no raw VLM output trusted without validation

## 13. Scalability Assumptions

This architecture is sized for:

- solo-developer MVP
- user-scoped workloads
- up to approximately 1,000 problems per user as stated in the requirements

At this scale, a modular monolith with indexed MongoDB queries is sufficient.

## 14. Explicit Non-Goals for This Architecture

This design does not optimize for:

- classroom-scale multi-user sharing
- cross-tenant analytics
- offline-first operation
- native mobile clients
- microservice decomposition
- advanced scheduling algorithms beyond stated weighted selection policy

## 15. Key Architectural Decisions to Freeze

1. LearnLoop is a modular monolith, not a distributed system.
2. The browser never talks directly to the VLM or object store using permanent credentials.
3. Exams are historical snapshots; problems are current editable records.
4. One active exam per user is enforced server-side and in the data model.
5. Ingestion preview is a first-class temporary resource.
6. Graph DSL is always handled as untrusted input and rendered only in a sandbox.
7. MongoDB deployment must support transaction-backed submission.
