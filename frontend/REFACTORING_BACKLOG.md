# Frontend Refactoring Backlog (Archived)

> **Status:** Archived on 2026-07-07.
>
> The original entries below have been completed or made obsolete by subsequent
> frontend work. This file is kept as an archive note so future readers can see
> what was tracked and where the completed work now lives. New frontend
> refactoring opportunities should be proposed as separate GitHub issues instead
> of being added here.

## Archive Evidence

Each row maps an original backlog entry to its current state in the codebase,
with pointers that can be spot-checked to confirm the work is done.

| # | Original entry | Status | Evidence |
|---|----------------|--------|----------|
| 1 | Unify API import style (named `api` import; remove default export) | Complete | `src/contexts/AuthContext.tsx:4` uses `import { api, type User } from "@/api/client"`; `src/api/client.ts` has no `export default` (only `export const api`, `export class ApiError`, and interface exports) |
| 2 | IngestionWizard API client consistency / add `postFormData` | Complete / obsolete | `src/api/client.ts:203` defines `postFormData<T>(path, formData)`; `src/api/bulkIngestion.ts:29` uses `api.postFormData<BatchResponse>(...)`; the original `src/components/IngestionWizard.tsx` no longer exists (replaced by `BulkIngestionWizard.tsx`, which has no raw `fetch` calls) |
| 3 | Problem interface unification (`types/problem.ts`) | Complete | `src/types/problem.ts` exports canonical `ProblemDetail`, `ProblemResponse`, `ProblemListItem`, `PracticeWeight`, `ProblemsResponse`; consumed by `src/pages/ProblemDetailPage.tsx:13`, `src/pages/CoachingPage.tsx:10`, `src/pages/ProblemsPage.tsx:9`; no duplicated `Problem` interface remains |
| 4 | Shared `PROBLEM_TYPE_OPTIONS` constant | Complete | `src/constants/problemTypes.ts:1` defines `PROBLEM_TYPE_OPTIONS`; consumed by `src/pages/ProblemDetailPage.tsx:14` and `src/pages/ProblemsPage.tsx:11` (via `PROBLEM_TYPE_FILTER_OPTIONS`) |
| 5 | Fragile 404 error detection -> typed `ApiError` | Complete | `src/api/client.ts:8` exports `class ApiError extends Error` with a `status: number` field; `src/pages/ActiveExamPage.tsx:204` and `src/components/BulkIngestionWizard.tsx:165` use `instanceof ApiError && .status === 404` instead of string matching; no `message.includes("404")` remains |

## Notes

- This archive records only the originally tracked items. It is not a new backlog.
- Evidence pointers reflect the codebase at archive time (commit `1226b7e`,
  2026-07-07) and may drift as the codebase evolves; search by symbol name to
  re-locate any pointer.

## Change Log

- **2026-05-24**: Initial backlog created after Batch 1 refactoring.
- **2026-07-07**: Backlog archived. All five original entries are complete or
  obsolete; see the evidence table above. New refactoring opportunities should be
  proposed as separate GitHub issues.
