import { useEffect, useMemo, useState } from "react";
import type { BulkBatch, BulkItem } from "@/types/bulkIngestion";

const POLL_INTERVAL_MS = 2500;

export interface BulkSubmitStepProps {
  batch: BulkBatch;
  isLoading: boolean;
  onSubmit: (batchId: string) => void | Promise<void>;
  onRefresh?: (batchId: string) => void | Promise<void>;
  onRetry: (itemId: string) => void | Promise<void>;
  onDelete: (itemId: string) => void | Promise<void>;
}

function statusLabel(status: string): string {
  switch (status) {
    case "queued":
      return "Queued";
    case "extracting":
      return "Extracting...";
    case "ready":
      return "Ready to submit";
    case "failed":
      return "Extraction failed";
    case "submit-failed":
      return "Submit failed";
    case "deleted":
      return "Deleted";
    case "submitted":
      return "Submitted";
    default:
      return status;
  }
}

function itemDisabledReasons(item: BulkItem): string[] {
  const reasons: string[] = [];
  if (item.status !== "ready") {
    reasons.push("Item is not ready");
  }
  if (!item.draft.text || item.draft.text.trim() === "") {
    reasons.push("Question text is required");
  }
  if (!item.draft.problemType) {
    reasons.push("Problem type is required");
  }
  if (!item.draft.correctAnswer || item.draft.correctAnswer.trim() === "") {
    reasons.push("Correct answer is required");
  }
  return reasons;
}

function isItemSubmittable(item: BulkItem): boolean {
  return itemDisabledReasons(item).length === 0;
}

export function BulkSubmitStep({
  batch,
  isLoading,
  onSubmit,
  onRefresh,
  onRetry,
  onDelete,
}: BulkSubmitStepProps) {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string>("");

  useEffect(() => {
    if (batch.status !== "active" || !onRefresh) return;

    const id = window.setInterval(() => {
      onRefresh(batch.id);
    }, POLL_INTERVAL_MS);

    return () => window.clearInterval(id);
  }, [batch.id, batch.status, onRefresh]);

  const items = useMemo(
    () => [...batch.items].sort((a, b) => a.order - b.order),
    [batch.items],
  );

  const activeItems = useMemo(
    () => items.filter((item) => item.status !== "deleted"),
    [items],
  );

  const submittableItems = useMemo(
    () => activeItems.filter(isItemSubmittable),
    [activeItems],
  );

  const canSubmit = activeItems.length > 0 && activeItems.length === submittableItems.length;

  const summary = useMemo(() => {
    return {
      total: activeItems.length,
      submitted: activeItems.filter((item) => item.status === "submitted").length,
      failed: activeItems.filter((item) => item.status === "submit-failed").length,
      ready: activeItems.filter((item) => item.status === "ready").length,
    };
  }, [activeItems]);

  const hasAttemptedSubmit = summary.submitted > 0 || summary.failed > 0;

  const handleSubmit = async () => {
    if (!canSubmit || isSubmitting) return;
    setIsSubmitting(true);
    setSubmitError("");
    try {
      await onSubmit(batch.id);
    } catch (err) {
      setSubmitError(err instanceof Error ? err.message : "Submit failed");
    } finally {
      setIsSubmitting(false);
    }
  };

  const disabledReasons = useMemo(() => {
    const reasons: string[] = [];
    if (activeItems.length === 0) {
      reasons.push("No items to submit");
    }
    activeItems.forEach((item) => {
      const itemReasons = itemDisabledReasons(item);
      if (itemReasons.length > 0) {
        reasons.push(`Item ${item.order + 1}: ${itemReasons.join(", ")}`);
      }
    });
    return reasons;
  }, [activeItems]);

  return (
    <div data-testid="bulk-wizard-submit-step">
      <h2>Submit items</h2>

      {submitError && (
        <div
          data-testid="bulk-submit-error"
          style={{ color: "var(--color-error, #dc2626)", marginBottom: "16px" }}
        >
          {submitError}
        </div>
      )}

      {hasAttemptedSubmit && (
        <div
          data-testid="bulk-submit-summary"
          style={{
            marginBottom: "16px",
            padding: "12px",
            borderRadius: "8px",
            backgroundColor: "var(--color-surface-muted)",
          }}
        >
          <div data-testid="bulk-submit-summary-total">Total: {summary.total}</div>
          <div data-testid="bulk-submit-summary-submitted">
            Submitted: {summary.submitted}
          </div>
          <div data-testid="bulk-submit-summary-failed">Failed: {summary.failed}</div>
          {summary.failed === 0 && summary.ready === 0 && (
            <div data-testid="bulk-submit-summary-complete">All items submitted</div>
          )}
        </div>
      )}

      <ul
        data-testid="bulk-submit-queue"
        style={{ listStyle: "none", padding: 0, marginBottom: "16px" }}
      >
        {activeItems.map((item) => {
          const reasons = itemDisabledReasons(item);
          const isSubmittable = reasons.length === 0;
          return (
            <li
              key={item.itemId}
              data-testid={`bulk-submit-item-${item.itemId}`}
              style={{
                padding: "12px",
                marginBottom: "8px",
                border: "1px solid var(--color-border)",
                borderRadius: "8px",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                }}
              >
                <span data-testid={`bulk-submit-item-status-${item.itemId}`}>
                  Item {item.order + 1}: {statusLabel(item.status)}
                </span>
                {item.status === "submit-failed" && (
                  <div style={{ display: "flex", gap: "8px" }}>
                    <button
                      type="button"
                      data-testid={`bulk-submit-retry-${item.itemId}`}
                      onClick={() => onRetry(item.itemId)}
                      disabled={isLoading || isSubmitting}
                    >
                      Retry
                    </button>
                    <button
                      type="button"
                      data-testid={`bulk-submit-delete-${item.itemId}`}
                      onClick={() => onDelete(item.itemId)}
                      disabled={isLoading || isSubmitting}
                    >
                      Delete
                    </button>
                  </div>
                )}
              </div>
              {!isSubmittable && (
                <ul
                  data-testid={`bulk-submit-item-reasons-${item.itemId}`}
                  style={{
                    color: "var(--color-error, #dc2626)",
                    fontSize: "0.9em",
                    marginTop: "8px",
                    marginBottom: 0,
                    paddingLeft: "20px",
                  }}
                >
                  {reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              )}
              {item.submit.failureMessage && (
                <div
                  data-testid={`bulk-submit-item-failure-${item.itemId}`}
                  style={{
                    color: "var(--color-error, #dc2626)",
                    fontSize: "0.9em",
                    marginTop: "8px",
                  }}
                >
                  {item.submit.failureMessage}
                </div>
              )}
              {item.submit.submittedProblemId && (
                <div
                  data-testid={`bulk-submit-item-problem-${item.itemId}`}
                  style={{ fontSize: "0.9em", marginTop: "8px" }}
                >
                  Problem {item.submit.submittedProblemId}
                </div>
              )}
            </li>
          );
        })}
      </ul>

      {!canSubmit && disabledReasons.length > 0 && (
        <div
          data-testid="bulk-submit-disabled-reasons"
          style={{ marginBottom: "16px", fontSize: "0.9em" }}
        >
          {disabledReasons.map((reason) => (
            <div key={reason} data-testid="bulk-submit-disabled-reason">
              {reason}
            </div>
          ))}
        </div>
      )}

      <button
        type="button"
        data-testid="bulk-submit-button"
        onClick={handleSubmit}
        disabled={!canSubmit || isLoading || isSubmitting}
      >
        {isSubmitting ? "Submitting..." : "Submit all items"}
      </button>
    </div>
  );
}
