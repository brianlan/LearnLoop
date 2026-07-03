import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent } from "react";
import { ApiError } from "@/api/client";
import {
  createBatch,
  getActiveBatch,
  getBatch,
  uploadBatchImages,
} from "@/api/bulkIngestion";
import type { BulkBatch, BulkWizardStep } from "@/types/bulkIngestion";

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
  const fileInputRef = useRef<HTMLInputElement | null>(null);

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
    try {
      const response = await createBatch();
      setBatchAndStep(response.batch);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to create batch");
    } finally {
      setIsLoading(false);
    }
  }, [setBatchAndStep]);

  const handleFileSelection = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const files = Array.from(event.target.files ?? []);
      event.target.value = "";
      if (files.length === 0 || !batch) return;

      setIsLoading(true);
      setError("");
      try {
        const response = await uploadBatchImages(batch.id, files);
        setBatchAndStep(response.batch);
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to upload images");
      } finally {
        setIsLoading(false);
      }
    },
    [batch, setBatchAndStep],
  );

  const handleStartUpload = useCallback(() => {
    fileInputRef.current?.click();
  }, []);

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
          <div data-testid="bulk-wizard-upload-step">
            <h2>Upload images</h2>
            {batch ? (
              <>
                <p>Add images to your batch.</p>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept="image/*"
                  multiple
                  onChange={handleFileSelection}
                  data-testid="bulk-wizard-upload-input"
                  style={{ display: "none" }}
                />
                <button type="button" onClick={handleStartUpload}>
                  Choose image files
                </button>
              </>
            ) : (
              <>
                <p>No active batch found. Create one to get started.</p>
                <button type="button" onClick={handleCreateBatch} data-testid="bulk-wizard-create-batch">
                  Create batch
                </button>
              </>
            )}
          </div>
        )}

        {step === "detect" && (
          <div data-testid="bulk-wizard-detect-step">
            <h2>Review detected boxes</h2>
            <p>Box editor will be added in a later step.</p>
          </div>
        )}

        {step === "review" && (
          <div data-testid="bulk-wizard-review-step">
            <h2>Review extracted items</h2>
            <p>Item review UI will be added in a later step.</p>
          </div>
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
