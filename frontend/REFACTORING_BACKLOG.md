# Frontend Refactoring Backlog

This document tracks refactoring opportunities identified but deferred for future work.

## Batch 2 — Deferred Items

### 1. Unify API Import Style (Complete)

**Status:** Partially done
**Files:** `api/client.ts`, `contexts/AuthContext.tsx`

**Remaining work:**
- Update `AuthContext.tsx` to use named import `import { api } from "@/api/client"`
- Remove the default export from `client.ts`

**Reason deferred:** AuthContext was outside the scope of the current refactoring batch. Requires separate change.

---

### 2. IngestionWizard API Client Consistency

**Status:** Deferred
**Files:** `components/IngestionWizard.tsx`, `api/client.ts`

**Problem:**
IngestionWizard bypasses the API client for some network calls:
- `createPreview()` uses raw `fetch()` with FormData
- `updatePreview()` uses raw `fetch()`
- `retryPreview()` uses raw `fetch()`
- `confirmPreview()` uses raw `fetch()`

Meanwhile, `getPreview()` correctly uses `api.get()`.

**Impact:**
- Inconsistent error handling (raw fetch loses `code` and `status` fields)
- Hardcoded `/api/v1` prefix in raw calls
- Future changes to API client (auth, retries, logging) won't apply to these calls

**Recommended approach:**
1. Add `postFormData(url: string, formData: FormData)` method to API client
2. Migrate IngestionWizard to use `api.postFormData()`, `api.patch()`, `api.post()`

**Risk level:** Medium — changes network call behavior and error handling

---

### 3. Problem Interface Unification

**Status:** Deferred
**Files:** `pages/ProblemsPage.tsx`, `pages/ProblemDetailPage.tsx`, `types/exam.ts`

**Problem:**
`Problem` interface is defined inline in two places with different field subsets:
- `ProblemsPage.tsx` lines 7-23
- `ProblemDetailPage.tsx` lines 19-30

`CorrectAnswer` interface is duplicated between `ProblemDetailPage.tsx` and `types/exam.ts`.

**Recommended approach:**
Create `types/problem.ts` with canonical `Problem` interface that is the union of both. Pages use a single import. If a page only needs a subset, use `Pick<Problem, ...>`.

**Reason deferred:** Requires verifying which fields each page actually depends on from API responses.

---

### 4. Shared PROBLEM_TYPE_OPTIONS Constant

**Status:** Deferred
**Files:** `pages/ProblemsPage.tsx`, `components/IngestionWizard.tsx`

**Problem:**
Problem type options are defined in multiple places:
- `ProblemsPage.tsx` has `PROBLEM_TYPE_OPTIONS` constant
- `IngestionWizard.tsx` hardcodes `<option>` values in JSX

**Recommended approach:**
Define `PROBLEM_TYPE_OPTIONS` once (in `types/` or shared constants file) and import in both places.

**Risk level:** Low

---

### 5. Fragile 404 Error Detection

**Status:** Deferred
**Files:** `pages/ActiveExamPage.tsx`, `api/client.ts`

**Problem:**
ActiveExamPage detects 404 errors by string matching:
```tsx
const isNotFoundError = examError instanceof Error && examError.message.includes("404");
```

This relies on the HTTP status code being embedded in the error message string. A change to error message formatting would break this detection.

**Recommended approach:**
Export a typed `ApiError` class with `status` field, so consumers can check `err.status === 404` instead of string matching.

**Reason deferred:** Changes error handling contract. Requires human judgment on whether this affects business flow (showing "no active exam" vs "error").

**Risk level:** Medium

---

## Summary

| Item | Priority | Risk | Effort |
|------|----------|------|--------|
| API import style (AuthContext) | P2 | Low | Small |
| IngestionWizard API client | P1 | Medium | Medium |
| Problem interface unification | P2 | Low | Small |
| PROBLEM_TYPE_OPTIONS constant | P2 | Low | Small |
| 404 error detection | P2 | Medium | Small |

---

## Change Log

- **2026-05-24**: Initial backlog created after Batch 1 refactoring
