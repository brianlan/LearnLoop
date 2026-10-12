import { useEffect, useState } from "react";
import { api } from "@/api/client";

export interface GraphDslValidationState {
  issues: string[];
  isValidating: boolean;
  isValid: boolean;
  isUnavailable: boolean;
}

// Matches the existing autosave debounce cadence in BulkReviewStep.editing.
const DEBOUNCE_MS = 500;

/**
 * Debounced pre-save graphDsl validation (issue #695).
 *
 * Empty DSL is valid because a graph is optional. A successful response is the
 * only passing state: pending, invalid, and request-failure states all keep
 * `isValid` false so callers cannot treat unknown validity as valid. Stale
 * in-flight requests are aborted when the DSL changes again.
 */
export function useGraphDslValidation(
  dsl: string | null | undefined,
): GraphDslValidationState {
  const value = dsl ?? "";
  const [issues, setIssues] = useState<string[]>([]);
  const [isValidating, setIsValidating] = useState(false);
  const [isValid, setIsValid] = useState(false);
  const [isUnavailable, setIsUnavailable] = useState(false);

  useEffect(() => {
    if (!value.trim()) {
      setIssues([]);
      setIsValidating(false);
      setIsValid(true);
      setIsUnavailable(false);
      return;
    }

    setIsValidating(true);
    setIsUnavailable(false);
    // ponytail: effect-local controller — React aborts the previous effect
    // before running this one, so no cross-effect ref is needed.
    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      api
        .validateGraphDsl(value, { signal: controller.signal })
        .then((result) => {
          if (controller.signal.aborted) return;
          setIssues(result.valid ? [] : result.issues);
          setIsValid(result.valid);
          setIsUnavailable(false);
        })
        .catch(() => {
          // Validation is unavailable, not valid. Do not fabricate sanitizer
          // reasons; callers must keep the save gate closed.
          if (controller.signal.aborted) return;
          setIssues([]);
          setIsValid(false);
          setIsUnavailable(true);
        })
        .finally(() => {
          if (!controller.signal.aborted) setIsValidating(false);
        });
    }, DEBOUNCE_MS);

    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [value]);

  return { issues, isValidating, isValid, isUnavailable };
}
