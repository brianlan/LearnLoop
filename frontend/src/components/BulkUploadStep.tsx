import type { BulkBatch } from "@/types/bulkIngestion";

export interface BulkUploadStepProps {
  batch: BulkBatch | null;
  isLoading: boolean;
  error?: string;
  onCreateBatch: () => void;
  onUpload: (files: FileList | null) => void;
}

export function BulkUploadStep({
  batch,
  isLoading,
  error,
  onCreateBatch,
  onUpload,
}: BulkUploadStepProps) {
  return (
    <div data-testid="bulk-wizard-upload-step">
      <h2>Upload images</h2>
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
          <p>Add images to your batch.</p>
          {batch.images.length > 0 && (
            <ul data-testid="bulk-upload-image-list">
              {batch.images.map((image) => (
                <li key={image.imageId}>{image.sourceImage.objectKey || image.imageId}</li>
              ))}
            </ul>
          )}
          <input
            type="file"
            accept="image/*"
            multiple
            data-testid="bulk-wizard-upload-input"
            onChange={(event) => onUpload(event.target.files)}
            style={{ display: "none" }}
          />
          <label>
            <button
              type="button"
              data-testid="bulk-wizard-upload-button"
              onClick={() => {
                const input = document.querySelector<HTMLInputElement>(
                  '[data-testid="bulk-wizard-upload-input"]',
                );
                input?.click();
              }}
              disabled={isLoading}
            >
              Choose image files
            </button>
          </label>
        </>
      ) : (
        <>
          <p>No active batch found. Create one to get started.</p>
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
