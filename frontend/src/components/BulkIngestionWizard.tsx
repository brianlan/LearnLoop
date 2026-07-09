import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/api/client";
import {
  commitImage,
  createBatch,
  deleteBatchItem,
  deleteImage,
  detectImageBoxes,
  getActiveBatch,
  getBatch,
  retryItem,
  saveImageBoxes,
  startBatchExtraction,
  submitBatch,
  undoDeleteBatchItem,
  updateItemDraft,
  uploadBatchImages,
} from "@/api/bulkIngestion";
import type {
  BulkBatch,
  BulkDraft,
  BulkImageBox,
  BulkWizardStep,
} from "@/types/bulkIngestion";
import { BulkUploadStep } from "./BulkUploadStep";
import { BulkDetectStep } from "./BulkDetectStep";
import { BulkReviewStep } from "./BulkReviewStep";
import { BulkSubmitStep } from "./BulkSubmitStep";

export type { BulkWizardStep };

const BATCH_EXPIRED_STATUS = 409;
const BATCH_EXPIRED_CODE = "BATCH_EXPIRED";

function isBatchExpiredError(err: unknown): err is ApiError {
  return (
    err instanceof ApiError &&
    err.status === BATCH_EXPIRED_STATUS &&
    err.code === BATCH_EXPIRED_CODE
  );
}

interface BulkIngestionWizardProps {
  initialBatchId?: string;
  onComplete?: () => void;
  onCancel?: () => void;
  tagSuggestions?: string[];
}

function deriveStep(batch: BulkBatch): BulkWizardStep {
  if (batch.status === "expired") {
    return "complete";
  }
  if (batch.status !== "active") {
    return "complete";
  }
  if (batch.images.length === 0) {
    return "upload";
  }
  if (batch.images.some((image) => image.status !== "committed")) {
    return "detect";
  }
  if (
    batch.items.some(
      (item) =>
        item.status === "queued" ||
        item.status === "extracting" ||
        item.status === "failed" ||
        item.status === "ready",
    )
  ) {
    return "review";
  }
  if (
    batch.items.some(
      (item) => item.status === "submit-failed",
    )
  ) {
    return "submit";
  }
  return "complete";
}

function canPreserveSubmitStep(batch: BulkBatch): boolean {
  if (batch.status !== "active") return false;
  if (batch.images.some((image) => image.status !== "committed")) return false;
  return batch.items.some(
    (item) =>
      item.status === "ready" ||
      item.status === "submit-failed" ||
      item.status === "submitted",
  );
}

function canPreserveReviewStep(batch: BulkBatch): boolean {
  if (batch.status !== "active") return false;
  if (batch.images.some((image) => image.status !== "committed")) return false;
  return batch.items.some(
    (item) => item.status !== "deleted" && item.status !== "submitted",
  );
}

export function BulkIngestionWizard({
  initialBatchId,
  onComplete,
  onCancel,
  tagSuggestions = [],
}: BulkIngestionWizardProps) {
  const [batch, setBatch] = useState<BulkBatch | null>(null);
  const [step, setStep] = useState<BulkWizardStep>("upload");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string>("");
  const [uploadError, setUploadError] = useState<string>("");
  const extractionStartedForBatch = useRef<Set<string>>(new Set());

  const setBatchAndStep = useCallback((nextBatch: BulkBatch) => {
    setBatch(nextBatch);
    setStep((currentStep) => {
      if (currentStep === "submit" && canPreserveSubmitStep(nextBatch)) {
        return "submit";
      }
      if (currentStep === "review" && canPreserveReviewStep(nextBatch)) {
        return "review";
      }
      return deriveStep(nextBatch);
    });
  }, []);

  const handleContinueToSubmit = useCallback(() => {
    setStep("submit");
  }, []);

  const handleBackToReview = useCallback(() => {
    setStep("review");
  }, []);

  const handleExpiredBatch = useCallback(async () => {
    if (!batch) {
      setError("This batch has expired.");
      return;
    }
    try {
      const response = await getBatch(batch.id);
      setBatchAndStep(response.batch);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "This batch has expired.");
    }
  }, [batch, setBatchAndStep]);

  const handleMutationError = useCallback(
    async (err: unknown, fallbackMessage: string) => {
      if (isBatchExpiredError(err)) {
        await handleExpiredBatch();
      } else {
        setError(err instanceof Error ? err.message : fallbackMessage);
      }
    },
    [handleExpiredBatch],
  );

  useEffect(() => {
    let cancelled = false;

    async function loadBatch() {
      try {
        const response = initialBatchId
          ? await getBatch(initialBatchId)
          : await getActiveBatch();
        if (!cancelled) {
          setBatchAndStep(response.batch);
          setError("");
        }
      } catch (err) {
        if (cancelled) return;
        if (err instanceof ApiError && err.status === 404) {
          setBatch(null);
          setStep("upload");
          setError("");
        } else {
          setError(err instanceof Error ? err.message : "Failed to load batch");
        }
      } finally {
        if (!cancelled) setIsLoading(false);
      }
    }

    loadBatch();
    return () => {
      cancelled = true;
    };
  }, [initialBatchId, setBatchAndStep]);

  const handleCreateBatch = useCallback(async () => {
    setIsLoading(true);
    setError("");
    setUploadError("");
    try {
      const response = await createBatch();
      setBatchAndStep(response.batch);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create batch");
    } finally {
      setIsLoading(false);
    }
  }, [setBatchAndStep]);

  const handleUploadFiles = useCallback(
    async (files: FileList | null) => {
      const fileArray = Array.from(files ?? []);
      if (fileArray.length === 0 || !batch) return;

      setIsLoading(true);
      setUploadError("");
      try {
        const response = await uploadBatchImages(batch.id, fileArray);
        setBatchAndStep(response.batch);
      } catch (err) {
        if (isBatchExpiredError(err)) {
          await handleExpiredBatch();
        } else {
          setUploadError(
            err instanceof Error ? err.message : "Failed to upload images",
          );
        }
      } finally {
        setIsLoading(false);
      }
    },
    [batch, setBatchAndStep],
  );

  const handleDetect = useCallback(
    async (imageId: string) => {
      if (!batch) return;
      setError("");
      try {
        const response = await detectImageBoxes(batch.id, imageId);
        setBatchAndStep(response.batch);
      } catch (err) {
        await handleMutationError(err, "Failed to detect boxes");
      }
    },
    [batch, handleMutationError, setBatchAndStep],
  );

  const handleSaveBoxes = useCallback(
    async (imageId: string, boxes: BulkImageBox[], subject?: string | null) => {
      if (!batch) return;
      setError("");
      try {
        const response = await saveImageBoxes(batch.id, imageId, boxes, subject);
        setBatchAndStep(response.batch);
      } catch (err) {
        await handleMutationError(err, "Failed to save boxes");
      }
    },
    [batch, handleMutationError, setBatchAndStep],
  );

  const handleCommit = useCallback(
    async (imageId: string) => {
      if (!batch) return;
      setError("");
      try {
        const response = await commitImage(batch.id, imageId);
        setBatchAndStep(response.batch);
      } catch (err) {
        await handleMutationError(err, "Failed to commit image");
      }
    },
    [batch, handleMutationError, setBatchAndStep],
  );

  const handleDeleteImage = useCallback(
    async (imageId: string) => {
      if (!batch) return;
      setError("");
      try {
        const response = await deleteImage(batch.id, imageId);
        setBatchAndStep(response.batch);
      } catch (err) {
        await handleMutationError(err, "Failed to delete image");
      }
    },
    [batch, handleMutationError, setBatchAndStep],
  );

  const handleRefreshBatch = useCallback(
    async (batchId: string) => {
      try {
        const response = await getBatch(batchId);
        setBatchAndStep(response.batch);
      } catch (err) {
        if (isBatchExpiredError(err)) {
          await handleExpiredBatch();
        }
        // Polling failures are intentionally not surfaced as blocking errors.
      }
    },
    [handleExpiredBatch, setBatchAndStep],
  );

  const handleUpdateItemDraft = useCallback(
    async (itemId: string, draft: Partial<BulkDraft>) => {
      if (!batch) return;
      try {
        const response = await updateItemDraft(batch.id, itemId, draft);
        setBatchAndStep(response.batch);
      } catch (err) {
        await handleMutationError(err, "Failed to save draft");
      }
    },
    [batch, handleMutationError, setBatchAndStep],
  );

  const handleRetryItem = useCallback(
    async (itemId: string) => {
      if (!batch) return;
      setError("");
      try {
        const response = await retryItem(batch.id, itemId);
        setBatchAndStep(response.batch);
      } catch (err) {
        await handleMutationError(err, "Failed to retry item");
      }
    },
    [batch, handleMutationError, setBatchAndStep],
  );

  const handleDeleteItem = useCallback(
    async (itemId: string) => {
      if (!batch) return;
      setError("");
      try {
        const response = await deleteBatchItem(batch.id, itemId);
        setBatchAndStep(response.batch);
      } catch (err) {
        await handleMutationError(err, "Failed to delete item");
      }
    },
    [batch, handleMutationError, setBatchAndStep],
  );

  const handleUndoDeleteItem = useCallback(
    async (itemId: string) => {
      if (!batch) return;
      setError("");
      try {
        const response = await undoDeleteBatchItem(batch.id, itemId);
        setBatchAndStep(response.batch);
      } catch (err) {
        await handleMutationError(err, "Failed to restore item");
      }
    },
    [batch, handleMutationError, setBatchAndStep],
  );

  const handleSubmit = useCallback(
    async (batchId: string) => {
      setError("");
      try {
        await submitBatch(batchId);
      } catch (err) {
        if (isBatchExpiredError(err)) {
          const response = await getBatch(batchId);
          setBatchAndStep(response.batch);
          return;
        }
        throw err;
      }
      const response = await getBatch(batchId);
      setBatchAndStep(response.batch);
    },
    [setBatchAndStep],
  );

  useEffect(() => {
    if (step !== "review" || !batch || batch.status !== "active") return;
    if (!batch.items.some((item) => item.status === "queued")) return;
    const batchId = batch.id;
    if (extractionStartedForBatch.current.has(batchId)) return;
    extractionStartedForBatch.current.add(batchId);

    let cancelled = false;
    async function kickoff() {
      try {
        await startBatchExtraction(batchId);
        if (!cancelled) {
          const response = await getBatch(batchId);
          if (!cancelled) setBatchAndStep(response.batch);
        }
      } catch (err) {
        if (cancelled) return;
        if (isBatchExpiredError(err)) {
          await handleExpiredBatch();
          return;
        }
        setError(
          err instanceof Error ? err.message : "Failed to start extraction",
        );
      }
    }
    kickoff();
    return () => {
      cancelled = true;
    };
  }, [step, batch?.id, handleExpiredBatch]);

  if (isLoading) {
    return (
      <div data-testid="bulk-wizard-loading" style={{ padding: "24px", textAlign: "center" }}>
        Loading...
      </div>
    );
  }

  if (error) {
    return (
      <div
        data-testid="bulk-wizard-error"
        style={{
          padding: "24px",
          color: "var(--color-error, #dc2626)",
          textAlign: "center",
        }}
      >
        {error}
        {onCancel && (
          <div style={{ marginTop: "16px" }}>
            <button type="button" onClick={onCancel}>
              Cancel
            </button>
          </div>
        )}
      </div>
    );
  }

  if (batch?.status === "expired") {
    return (
      <div data-testid="bulk-wizard-expired" style={{ padding: "24px", textAlign: "center" }}>
        <p>This batch has expired.</p>
        <p style={{ fontSize: "0.9em", color: "var(--color-text-muted)" }}>
          Your work could not be saved. Start a new ingestion session.
        </p>
        <button type="button" onClick={handleCreateBatch} style={{ marginTop: "16px" }}>
          Start a new batch
        </button>
      </div>
    );
  }

  if (batch?.status === "completed" || step === "complete") {
    const submittedCount =
      batch?.items.filter((item) => item.status === "submitted").length ?? 0;
    return (
      <div data-testid="bulk-wizard-complete" style={{ padding: "24px", textAlign: "center" }}>
        <p>{batch?.status === "completed" ? "Batch completed." : "All items submitted."}</p>
        <p
          data-testid="bulk-wizard-complete-count"
          style={{ fontSize: "0.9em", color: "var(--color-text-muted)" }}
        >
          {submittedCount} problem(s) created
        </p>
        <div style={{ display: "flex", gap: "12px", justifyContent: "center", marginTop: "16px" }}>
          <button type="button" onClick={handleCreateBatch} data-testid="bulk-wizard-start-new">
            Start a new batch
          </button>
          {onComplete && (
            <button type="button" onClick={onComplete} data-testid="bulk-wizard-finish">
              Finish
            </button>
          )}
        </div>
      </div>
    );
  }

  const steps: { key: BulkWizardStep; label: string }[] = [
    { key: "upload", label: "Upload" },
    { key: "detect", label: "Detect" },
    { key: "review", label: "Review" },
    { key: "submit", label: "Submit" },
    { key: "complete", label: "Complete" },
  ];

  return (
    <div
      data-testid="bulk-ingestion-wizard"
      style={{
        maxWidth: "800px",
        margin: "0 auto",
        padding: "24px",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          marginBottom: "32px",
          gap: "8px",
        }}
      >
        {steps.map((s, index, arr) => {
          const stepIndex = steps.findIndex((st) => st.key === step);
          const isActive = s.key === step;
          const isCompleted = stepIndex > index;
          return (
            <div key={s.key} style={{ display: "flex", alignItems: "center" }}>
              <div
                style={{
                  padding: "8px 16px",
                  borderRadius: "20px",
                  fontSize: "13px",
                  fontWeight: 500,
                  backgroundColor: isActive
                    ? "var(--color-primary)"
                    : isCompleted
                      ? "var(--color-success, #16a34a)"
                      : "var(--color-border)",
                  color: isActive || isCompleted ? "white" : "var(--color-text-muted)",
                }}
              >
                {s.label}
              </div>
              {index < arr.length - 1 && (
                <div
                  style={{
                    width: "24px",
                    height: "2px",
                    backgroundColor: isCompleted
                      ? "var(--color-success, #16a34a)"
                      : "var(--color-border)",
                    margin: "0 4px",
                  }}
                />
              )}
            </div>
          );
        })}
      </div>

      <div
        style={{
          backgroundColor: "var(--color-surface)",
          borderRadius: "12px",
          boxShadow: "0 1px 3px rgba(0, 0, 0, 0.1)",
          border: "1px solid var(--color-border)",
          padding: "24px",
        }}
      >
        {step === "upload" && (
          <BulkUploadStep
            batch={batch}
            isLoading={isLoading}
            error={uploadError}
            onCreateBatch={handleCreateBatch}
            onUpload={handleUploadFiles}
          />
        )}

        {step === "detect" && batch && (
          <BulkDetectStep
            batch={batch}
            isLoading={isLoading}
            onDetect={handleDetect}
            onSaveBoxes={handleSaveBoxes}
            onCommit={handleCommit}
            onDelete={handleDeleteImage}
          />
        )}

        {step === "review" && batch && (
          <BulkReviewStep
            batch={batch}
            isLoading={isLoading}
            onRefresh={handleRefreshBatch}
            onUpdateDraft={handleUpdateItemDraft}
            onRetry={handleRetryItem}
            onDelete={handleDeleteItem}
            onUndoDelete={handleUndoDeleteItem}
            onContinue={handleContinueToSubmit}
            tagSuggestions={tagSuggestions}
          />
        )}

        {step === "submit" && batch && (
          <BulkSubmitStep
            batch={batch}
            isLoading={isLoading}
            onSubmit={handleSubmit}
            onRefresh={handleRefreshBatch}
            onRetry={handleRetryItem}
            onDelete={handleDeleteItem}
            onBackToReview={handleBackToReview}
          />
        )}
      </div>

      {onCancel && (
        <div style={{ marginTop: "16px", textAlign: "right" }}>
          <button type="button" onClick={onCancel}>
            Cancel
          </button>
        </div>
      )}
    </div>
  );
}
