import { useRef } from "react";
import type { BulkBatch, IngestionMode } from "@/types/bulkIngestion";

export const INGESTION_MODE_OPTIONS: {
  value: IngestionMode;
  label: string;
  description: string;
}[] = [
  {
    value: "original",
    label: "Original",
    description: "Ingest the problems exactly as they are.",
  },
  {
    value: "data-only",
    label: "Variant (data-only)",
    description:
      "Generate a new practice problem with different numbers while keeping the wording, roles, and what is asked.",
  },
  {
    value: "transfer-variant",
    label: "Transfer Variant",
    description:
      "Generate a genuinely different-looking problem that tests the same core concept and uses essentially the same solution strategy.",
  },
];

// Legacy persisted batches keep their historical mode value; the safe label
// avoids implying they were validated against the Transfer Variant standard (#656).
const LEGACY_INGESTION_MODE_LABELS: Partial<Record<IngestionMode, string>> = {
  "data-and-wording": "Variant (legacy)",
};

export function ingestionModeLabel(mode: IngestionMode): string {
  return (
    INGESTION_MODE_OPTIONS.find((option) => option.value === mode)?.label ??
    LEGACY_INGESTION_MODE_LABELS[mode] ??
    mode
  );
}

export interface BulkUploadStepProps {
  batch: BulkBatch | null;
  isLoading: boolean;
  error?: string;
  mode: IngestionMode;
  onModeChange: (mode: IngestionMode) => void;
  onCreateBatch: () => void;
  onUpload: (files: FileList | null) => void;
}

export function BulkUploadStep({
  batch,
  isLoading,
  error,
  mode,
  onModeChange,
  onCreateBatch,
  onUpload,
}: BulkUploadStepProps) {
  const inputRef = useRef<HTMLInputElement>(null);

  return (
    <div data-testid="bulk-wizard-upload-step">
      <h2>Upload images or PDFs</h2>
      {error && (
        <div
          data-testid="bulk-upload-error"
          style={{ color: "var(--color-error, #dc2626)", marginBottom: "16px" }}
        >
          {error}
        </div>
      )}
      {batch ? (
        <>
          <p
            data-testid="bulk-wizard-mode-locked"
            style={{ color: "var(--color-text-muted)" }}
          >
            Ingestion mode: {ingestionModeLabel(batch.ingestionMode)} (locked
            for this batch)
          </p>
          <p>Add images or PDFs to your batch.</p>
          {batch.images.length > 0 && (
            <ul data-testid="bulk-upload-image-list">
              {batch.images.map((image) => (
                <li key={image.imageId}>{image.sourceImage.objectKey || image.imageId}</li>
              ))}
            </ul>
          )}
          <input
            ref={inputRef}
            type="file"
            accept="image/*,application/pdf,.pdf"
            multiple
            data-testid="bulk-wizard-upload-input"
            onChange={(event) => {
              onUpload(event.target.files);
              event.target.value = "";
            }}
            style={{ display: "none" }}
          />
          <button
            type="button"
            data-testid="bulk-wizard-upload-button"
            onClick={() => inputRef.current?.click()}
            disabled={isLoading}
          >
            Choose files
          </button>
        </>
      ) : (
        <>
          <p>No active batch found. Create one to get started.</p>
          <fieldset
            style={{ border: "none", padding: 0, margin: "0 0 16px" }}
          >
            <legend
              style={{
                fontWeight: 500,
                marginBottom: "8px",
                padding: 0,
              }}
            >
              Ingestion mode
            </legend>
            {INGESTION_MODE_OPTIONS.map((option) => (
              <label
                key={option.value}
                style={{
                  display: "block",
                  marginBottom: "8px",
                  cursor: isLoading ? "default" : "pointer",
                }}
              >
                <input
                  type="radio"
                  name="ingestion-mode"
                  value={option.value}
                  data-testid={`bulk-wizard-mode-${option.value}`}
                  checked={mode === option.value}
                  disabled={isLoading}
                  onChange={() => onModeChange(option.value)}
                  style={{ marginRight: "8px" }}
                />
                <strong>{option.label}</strong> — {option.description}
              </label>
            ))}
          </fieldset>
          <button
            type="button"
            onClick={onCreateBatch}
            disabled={isLoading}
            data-testid="bulk-wizard-create-batch"
          >
            Create batch
          </button>
        </>
      )}
    </div>
  );
}
