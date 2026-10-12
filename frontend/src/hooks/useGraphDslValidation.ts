import { useEffect, useRef, useState } from "react";
import { api } from "@/api/client";

export interface GraphDslValidationState {
  issues: string[];
  isValidating: boolean;
}

// Matches the existing autosave debounce cadence in BulkReviewStep.editing.
const DEBOUNCE_MS = 500;

/**
 * Debounced pre-save graphDsl validation (issue #695).
 *
 * Empty DSL is valid because a graph is optional. Stale in-flight requests are
 * aborted when the DSL changes again, so an older response can never overwrite
 * newer feedback.
 */
export function useGraphDslValidation(
  dsl: string | null | undefined,
): GraphDslValidationState {
  const value = dsl ?? "";
  const [issues, setIssues] = useState<string[]>([]);
  const [isValidating, setIsValidating] = useState(false);
  const controllerRef = useRef<AbortController | null>(null);

  useEffect(() => {
    controllerRef.current?.abort();

    if (!value.trim()) {
      controllerRef.current = null;
      setIssues([]);
      setIsValidating(false);
      return;
    }

    setIsValidating(true);
    const timeout = window.setTimeout(() => {
      const controller = new AbortController();
      controllerRef.current = controller;
      api
        .validateGraphDsl(value, { signal: controller.signal })
        .then((result) => {
          if (controller.signal.aborted) return;
          setIssues(result.valid ? [] : result.issues);
        })
        .catch(() => {
          // A failed validation must not block typing or fabricate reasons.
          if (controller.signal.aborted) return;
          setIssues([]);
        })
        .finally(() => {
          if (!controller.signal.aborted) setIsValidating(false);
        });
    }, DEBOUNCE_MS);

    return () => {
      window.clearTimeout(timeout);
      controllerRef.current?.abort();
    };
  }, [value]);

  return { issues, isValidating };
}
