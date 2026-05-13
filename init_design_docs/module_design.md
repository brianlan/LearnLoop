# LearnLoop Module / Subsystem Responsibility Design

## 1. Purpose

This document defines the internal module boundaries of LearnLoop and the responsibility of each subsystem. It is intended to keep the MVP architecture clean and extensible without over-engineering.

## 2. Module Design Principles

1. Each module owns one clear slice of responsibility.
2. Domain rules belong in domain/application modules, not controllers or adapters.
3. Infrastructure modules may fail; domain modules remain the authority on system state.
4. History-oriented concerns stay with the exam subsystem, not the problem subsystem.

## 3. Module Overview

LearnLoop should be partitioned into the following modules:

1. Authentication Module
2. Session Module
3. Ingestion Module
4. Problem Catalog Module
5. Graph Rendering Module
6. Exam Module
7. Grading Module
8. Tracking Module
9. Storage Module
10. VLM Integration Module
11. Observability Module

## 4. Module Responsibilities

## 4.1 Authentication Module

### Owns

- registration rules
- password hash verification
- username uniqueness checks
- login/logout orchestration

### Must Not Own

- session persistence details beyond delegation
- business workflows for problems or exams

## 4.2 Session Module

### Owns

- creation and invalidation of session records
- cookie/session token issuance
- session lookup and expiration checks
- current-user resolution for authenticated requests

### Must Not Own

- password verification
- resource authorization rules beyond identity lookup

## 4.3 Ingestion Module

### Owns

- intake of clipboard-uploaded image blobs
- creation of ingestion preview records
- orchestration of VLM extraction
- retry handling when extraction fails
- conversion of confirmed preview data into a final problem record

### Must Not Own

- long-term problem list filtering
- exam generation
- graph execution engine internals

### Notes

This module is the workflow owner for `WF-1`.

## 4.4 Problem Catalog Module

### Owns

- problem CRUD rules
- tag handling
- soft delete behavior
- retrieval of problem details and per-problem tracking view

### Must Not Own

- historical exam reconstruction
- selection policy for exams
- storage upload mechanics

## 4.5 Graph Rendering Module

### Owns

- isolated rendering of JSXGraph DSL
- pre-render validation of graph input
- safe communication between app UI and sandboxed renderer
- user-facing error states for invalid DSL
- iframe lifecycle reset on timeout or failure

### Must Not Own

- authoritative storage of problem data
- VLM extraction logic
- exam scoring

### Security Boundary

This module is a dedicated safety subsystem. Its job is not to decide whether graph content is correct; its job is to render only within a constrained sandbox and fail safely.

Chosen MVP mechanism:

- host the renderer in a dedicated iframe sandbox with `sandbox="allow-scripts"`
- do not allow same-origin access or top-level navigation privileges
- load JSXGraph only inside the iframe
- communicate with the parent page using a strict `postMessage` protocol
- validate incoming DSL against a denylist of clearly unsafe patterns before passing it to the iframe
- treat the denylist as defense-in-depth only; the iframe sandbox remains the primary security control

## 4.6 Exam Module

### Owns

- one-active-exam rule
- eligible problem selection request
- exam creation and state transitions
- answer persistence during an exam
- resume logic
- exam history retrieval

### Must Not Own

- password/session handling
- direct VLM call mechanics
- object storage details

### Notes

The Exam Module owns the exam record as the lifecycle source of truth.

## 4.7 Grading Module

### Owns

- canonical normalization rules for objective question grading
- short-answer grading orchestration via VLM module
- retry-once-then-pending-review behavior
- aggregation of per-item results into an exam score

### Must Not Own

- selection policy
- problem editing
- rendering behavior

## 4.8 Tracking Module

### Owns

- updates to per-problem tracking summary on exam submission
- derivation of exposure/correct/failure counters
- last-test timestamps and last-attempt correctness flag

### Must Not Own

- exam item grading logic
- problem CRUD
- historical score presentation beyond stored exam data

### Notes

This module updates current tracking summary but does not reconstruct historical attempts outside exam records.

## 4.9 Storage Module

### Owns

- uploads to object storage
- object metadata capture
- generation of server-mediated or signed image access paths
- object key naming conventions and privacy rules

### Must Not Own

- problem ownership logic
- authorization decisions independent of caller context
- VLM request composition

## 4.10 VLM Integration Module

### Owns

- request construction for extraction and grading
- provider-specific HTTP interactions
- structured response validation
- retry logic for retryable errors
- raw provider result capture for audit/debug
- prompt/schema versioning for extraction and grading contracts
- background completion write-back for long-running extraction requests that outlive the bounded synchronous wait window

### Must Not Own

- final business acceptance of extraction output
- user edits
- exam lifecycle transitions

## 4.11 Observability Module

### Owns

- structured logging schema
- auth event logging
- VLM request logging
- exam lifecycle event logging

### Must Not Own

- domain state mutation
- user-facing decisions

## 5. Cross-Module Interaction Rules

## 5.1 Ingestion Flow Ownership

`Ingestion Module`
→ uses `Storage Module`
→ uses `VLM Integration Module`
→ uses `Graph Rendering Module` indirectly via UI support
→ creates final problem through `Problem Catalog Module`

## 5.2 Exam Submission Ownership

`Exam Module`
→ invokes `Grading Module`
→ invokes `Tracking Module`
→ persists final state transactionally

## 5.3 History Rule

- `Problem Catalog Module` owns current problem state
- `Exam Module` owns historical snapshots
- `Tracking Module` owns summary counters

No module should rebuild past exam details from the current problem document.

## 6. Dependency Direction

Allowed dependency direction:

```text
Presentation -> Application/Domain -> Infrastructure Adapters
```

Disallowed dependency direction:

- infrastructure adapters calling back into controllers
- graph renderer deciding grading outcomes
- VLM adapter deciding final problem persistence

## 7. Future Extension Readiness

This module layout supports later additions such as:

- classroom/sharing features by extending ownership models
- richer selection policies by evolving the Exam and Tracking modules
- alternate graph representations by replacing internals of the Graph Rendering module
- alternate VLM providers by swapping the VLM Integration adapter

## 8. Module Boundary Decisions to Freeze

1. Ingestion preview handling belongs to the Ingestion Module, not the Problem Catalog Module.
2. Historical exam data belongs to the Exam Module, not to the current problem model.
3. Tracking is a separate responsibility from grading.
4. Graph safety is a dedicated subsystem concern, not an incidental UI detail.
5. VLM provider specifics are isolated behind one integration module.
