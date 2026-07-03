import { useCallback, useEffect, useState } from "react";
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

export type { BulkWizardStep };

interface BulkIngestionWizardProps {
  initialBatchId?: string;
  onComplete?: () => void;
  onCancel?: () => void;
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
        item.status === "submit-failed",
    )
  ) {
    return "review";
  }
  if (batch.items.some((item) => item.status === "ready")) {
    return "submit";
  }
  return "complete";
}

export function BulkIngestionWizard({
  initialBatchId,
  onComplete,
  onCancel,
}: BulkIngestionWizardProps) {
  const [batch, setBatch] = useState<BulkBatch | null>(null);
  const [step, setStep] = useState<BulkWizardStep>("upload");
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string>("");
  const [uploadError, setUploadError] = useState<string>("");

  const setBatchAndStep = useCallback((nextBatch: BulkBatch) => {
    setBatch(nextBatch);
    setStep(deriveStep(nextBatch));
  }, []);

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
        setUploadError(
          err instanceof Error ? err.message : "Failed to upload images",
        );
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
        setError(err instanceof Error ? err.message : "Failed to detect boxes");
      }
    },
    [batch, setBatchAndStep],
  );

  const handleSaveBoxes = useCallback(
    async (imageId: string, boxes: BulkImageBox[], subject?: string | null) => {
      if (!batch) return;
      setError("");
      try {
        const response = await saveImageBoxes(batch.id, imageId, boxes, subject);
        setBatchAndStep(response.batch);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to save boxes");
      }
    },
    [batch, setBatchAndStep],
  );

  const handleCommit = useCallback(
    async (imageId: string) => {
      if (!batch) return;
      setError("");
      try {
        const response = await commitImage(batch.id, imageId);
        setBatchAndStep(response.batch);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to commit image");
      }
    },
    [batch, setBatchAndStep],
  );

  const handleDeleteImage = useCallback(
    async (imageId: string) => {
      if (!batch) return;
      setError("");
      try {
        const response = await deleteImage(batch.id, imageId);
        setBatchAndStep(response.batch);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to delete image");
      }
    },
    [batch, setBatchAndStep],
  );

  const handleRefreshBatch = useCallback(
    async (batchId: string) => {
      try {
        const response = await getBatch(batchId);
        setBatchAndStep(response.batch);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to refresh batch");
      }
    },
    [setBatchAndStep],
  );

  const handleUpdateItemDraft = useCallback(
    async (itemId: string, draft: Partial<BulkDraft>) => {
      if (!batch) return;
      try {
        const response = await updateItemDraft(batch.id, itemId, draft);
        setBatchAndStep(response.batch);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to save draft");
      }
    },
    [batch, setBatchAndStep],
  );

  const handleRetryItem = useCallback(
    async (itemId: string) => {
      if (!batch) return;
      setError("");
      try {
        const response = await retryItem(batch.id, itemId);
        setBatchAndStep(response.batch);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to retry item");
      }
    },
    [batch, setBatchAndStep],
  );

  const handleDeleteItem = useCallback(
    async (itemId: string) => {
      if (!batch) return;
      setError("");
      try {
        const response = await deleteBatchItem(batch.id, itemId);
        setBatchAndStep(response.batch);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to delete item");
      }
    },
    [batch, setBatchAndStep],
  );

  const handleUndoDeleteItem = useCallback(
    async (itemId: string) => {
      if (!batch) return;
      setError("");
      try {
        const response = await undoDeleteBatchItem(batch.id, itemId);
        setBatchAndStep(response.batch);
      } catch (err) {
        setError(
          err instanceof Error ? err.message : "Failed to restore item",
        );
      }
    },
    [batch, setBatchAndStep],
  );

  useEffect(() => {
    if (step !== "review" || !batch || batch.status !== "active") return;
    let cancelled = false;
    async function kickoff() {
      try {
        await startBatchExtraction(batch!.id);
        if (!cancelled) {
          const response = await getBatch(batch!.id);
          if (!cancelled) setBatchAndStep(response.batch);
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error ? err.message : "Failed to start extraction",
          );
        }
      }
    }
    kickoff();
    return () => {
      cancelled = true;
    };
  }, [step, batch, setBatchAndStep]);

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
        <button type="button" onClick={handleCreateBatch} style={{ marginTop: "16px" }}>
          Start a new batch
        </button>
      </div>
    );
  }

  if (batch?.status === "completed" || step === "complete") {
    return (
      <div data-testid="bulk-wizard-complete" style={{ padding: "24px", textAlign: "center" }}>
        <p>All done.</p>
        {onComplete && (
          <button type="button" onClick={onComplete} style={{ marginTop: "16px" }}>
            Finish
          </button>
        )}
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
          />
        )}

        {step === "submit" && (
          <div data-testid="bulk-wizard-submit-step">
            <h2>Submit items</h2>
            <p>Submit UI will be added in a later step.</p>
          </div>
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
