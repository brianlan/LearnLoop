import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, ClipboardEvent } from "react";
import { GraphSandbox } from "./GraphSandbox";
import { TagInput } from "./TagInput";
import { LatexText } from "./LatexText";
import { api } from "@/api/client";
import { useTagSuggestions } from "@/hooks/useTagSuggestions";

interface PreviewSourceImage {
  bucket: string;
  objectKey: string;
  contentType?: string;
  sizeBytes?: number;
  sha256?: string;
  uploadedAt?: string;
}

interface PreviewDraft {
  text: string | null;
  problemType: string | null;
  graphDsl: string | null;
  correctAnswer: string | null;
  tags: string[];
}

interface PreviewExtraction {
  rawText?: string | null;
  rawProblemType?: string | null;
  rawGraphDsl?: string | null;
  rawCorrectAnswer?: string | null;
  rawTags?: string[];
  failureCode?: string | null;
  failureMessage?: string | null;
  [key: string]: unknown;
}

export interface IngestionPreview {
  id: string;
  status: "extracting" | "ready" | "vlm-failed" | "graph-error";
  sourceImage: PreviewSourceImage;
  draft: PreviewDraft;
  extraction: PreviewExtraction;
  createdAt: string;
  updatedAt: string;
  expiresAt: string;
}

interface PreviewResponse {
  preview: IngestionPreview;
}

function normalizePreviewResponse(data: unknown): IngestionPreview {
  const candidate = data as
    | PreviewResponse
    | (Partial<IngestionPreview> & {
        extractedText?: string;
        problemType?: string;
        graphDsl?: string;
        correctAnswer?: string;
        tags?: string[];
      });

  if (candidate && typeof candidate === "object" && "preview" in candidate) {
    return candidate.preview as IngestionPreview;
  }

  return {
    id: candidate.id ?? "",
    status: (candidate.status as IngestionPreview["status"]) ?? "extracting",
    sourceImage: (candidate.sourceImage as PreviewSourceImage) ?? {
      bucket: "",
      objectKey: "",
    },
    draft: candidate.draft ?? {
      text: candidate.extractedText ?? null,
      problemType: candidate.problemType ?? null,
      graphDsl: candidate.graphDsl ?? null,
      correctAnswer: candidate.correctAnswer ?? null,
      tags: candidate.tags ?? [],
    },
    extraction: candidate.extraction ?? {},
    createdAt: candidate.createdAt ?? "",
    updatedAt: candidate.updatedAt ?? "",
    expiresAt: candidate.expiresAt ?? "",
  };
}

function mapPreviewToFormData(preview: IngestionPreview) {
  return {
    text: preview.draft.text || "",
    problemType: preview.draft.problemType || "",
    graphDsl: preview.draft.graphDsl || "",
    correctAnswer: preview.draft.correctAnswer || "",
    tags: preview.draft.tags,
  };
}

export type WizardStep = "paste" | "uploading" | "preview" | "editing" | "confirming";

export interface WizardFormData {
  text: string;
  problemType: string;
  graphDsl: string;
  correctAnswer: string;
  tags: string[];
}

interface IngestionWizardProps {
  onConfirm?: (previewId: string) => void;
  onCancel?: () => void;
}

async function createPreview(file: File): Promise<IngestionPreview> {
  const formData = new FormData();
  formData.append("image", file);

  const response = await fetch("/api/v1/ingestion-previews", {
    method: "POST",
    credentials: "include",
    body: formData,
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.error?.message || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return normalizePreviewResponse(data);
}

async function getPreview(id: string): Promise<IngestionPreview> {
  const data = await api.get<PreviewResponse>(`/ingestion-previews/${id}`);
  return normalizePreviewResponse(data);
}

interface PreviewUpdateData {
  text?: string;
  problemType?: string;
  graphDsl?: string;
  correctAnswer?: string;
  tags?: string[];
}

async function updatePreview(id: string, data: PreviewUpdateData): Promise<IngestionPreview> {
  const response = await fetch(`/api/v1/ingestion-previews/${id}`, {
    method: "PATCH",
    headers: {
      "Content-Type": "application/json",
    },
    credentials: "include",
    body: JSON.stringify(data),
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.error?.message || `HTTP ${response.status}`);
  }

  const result = await response.json();
  return normalizePreviewResponse(result);
}

async function retryPreview(id: string): Promise<IngestionPreview> {
  const response = await fetch(`/api/v1/ingestion-previews/${id}/retry`, {
    method: "POST",
    credentials: "include",
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.error?.message || `HTTP ${response.status}`);
  }

  const data = await response.json();
  return normalizePreviewResponse(data);
}

async function confirmPreview(id: string): Promise<{ problemId: string }> {
  const response = await fetch(`/api/v1/ingestion-previews/${id}/confirm`, {
    method: "POST",
    credentials: "include",
  });

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.error?.message || `HTTP ${response.status}`);
  }

  const data = (await response.json()) as { problem: { id: string } };
  return { problemId: data.problem.id };
}

function useDebouncedCallback<T extends (...args: unknown[]) => unknown>(
  callback: T,
  delay: number
) {
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  return useCallback(
    (...args: Parameters<T>) => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current);
      }
      timeoutRef.current = setTimeout(() => {
        callback(...args);
      }, delay);
    },
    [callback, delay]
  );
}

// ── Step sub-components ──────────────────────────────────────────────

interface PasteStepProps {
  onPaste: (event: ClipboardEvent) => void;
  fileInputRef: React.RefObject<HTMLInputElement>;
  onFileSelection: (event: ChangeEvent<HTMLInputElement>) => void;
  error: string;
}

function PasteStep({ onPaste, fileInputRef, onFileSelection, error }: PasteStepProps) {
  return (
    <div
      style={{
        padding: "48px 32px",
        textAlign: "center",
        border: "2px dashed var(--color-border-muted)",
        borderRadius: "8px",
        backgroundColor: "var(--color-surface-muted)",
      }}
      onPaste={onPaste}
      tabIndex={0}
      role="region"
      aria-label="Paste image area"
    >
      <div style={{ marginBottom: "16px", fontSize: "48px" }}>📋</div>
      <h3 style={{ margin: "0 0 8px", fontSize: "18px", fontWeight: 600 }}>
        Paste an Image
      </h3>
      <p style={{ margin: 0, color: "var(--color-text-muted)", fontSize: "14px" }}>
        Copy an image and paste it here (Ctrl+V or Cmd+V)
      </p>
      <div style={{ marginTop: "16px" }}>
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          style={{
            padding: "8px 16px",
            backgroundColor: "var(--color-surface)",
            color: "var(--color-text)",
            border: "1px solid var(--color-border-muted)",
            borderRadius: "4px",
            cursor: "pointer",
          }}
        >
          Choose Image File
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="image/*"
          onChange={(e) => void onFileSelection(e)}
          style={{ display: "none" }}
        />
      </div>
      {error && (
        <div
          style={{
            marginTop: "16px",
            padding: "12px 16px",
            backgroundColor: "var(--color-danger-bg)",
            borderRadius: "6px",
            color: "var(--color-text-danger)",
            fontSize: "14px",
          }}
        >
          {error}
        </div>
      )}
    </div>
  );
}

interface UploadingStepProps {
  uploadProgress: number;
}

function UploadingStep({ uploadProgress }: UploadingStepProps) {
  return (
    <div style={{ padding: "48px 32px", textAlign: "center" }}>
      <div style={{ marginBottom: "16px", fontSize: "48px" }}>⏳</div>
      <h3 style={{ margin: "0 0 8px", fontSize: "18px", fontWeight: 600 }}>
        Uploading...
      </h3>
      <div
        style={{
          width: "100%",
          height: "8px",
          backgroundColor: "var(--color-border)",
          borderRadius: "4px",
          overflow: "hidden",
          marginTop: "16px",
        }}
      >
        <div
          style={{
            width: `${uploadProgress}%`,
            height: "100%",
            backgroundColor: "var(--color-primary)",
            transition: "width 0.3s ease",
          }}
        />
      </div>
      <p style={{ margin: "8px 0 0", color: "var(--color-text-muted)", fontSize: "14px" }}>
        {uploadProgress}%
      </p>
    </div>
  );
}

interface PreviewStepProps {
  preview: IngestionPreview | null;
  isLoading: boolean;
  onRetry: () => void;
  setFormData: React.Dispatch<React.SetStateAction<WizardFormData>>;
  setCurrentStep: (step: WizardStep) => void;
  error: string;
}

function PreviewStep({ preview, isLoading, onRetry, setFormData, setCurrentStep, error }: PreviewStepProps) {
  return (
    <div style={{ padding: "32px" }}>
      <h3 style={{ margin: "0 0 16px", fontSize: "18px", fontWeight: 600 }}>
        Processing Image
      </h3>
      {preview?.status === "extracting" && (
        <div style={{ textAlign: "center", padding: "32px" }}>
          <div style={{ fontSize: "32px", marginBottom: "16px" }}>🤖</div>
          <p style={{ color: "var(--color-text-muted)" }}>
            AI is analyzing the image and extracting problem data...
          </p>
          <div
            style={{
              width: "40px",
              height: "40px",
              border: "3px solid var(--color-border)",
              borderTopColor: "var(--color-primary)",
              borderRadius: "50%",
              margin: "16px auto",
              animation: "spin 1s linear infinite",
            }}
          />
        </div>
      )}
      {preview?.status === "vlm-failed" && (
        <div
          style={{
            padding: "16px",
            backgroundColor: "var(--color-danger-bg)",
            borderRadius: "6px",
            marginBottom: "16px",
          }}
        >
          <div style={{ color: "var(--color-text-danger)", fontWeight: 600, marginBottom: "8px" }}>
            ⚠️ Extraction Failed
          </div>
          <p style={{ color: "var(--color-text-danger-secondary)", fontSize: "14px", margin: "0 0 12px" }}>
            The AI was unable to extract problem data from the image.
          </p>
          {(preview?.extraction?.failureCode || preview?.extraction?.failureMessage) && (
            <details style={{ marginBottom: "12px" }}>
              <summary style={{ cursor: "pointer", color: "var(--color-text-danger-secondary)", fontSize: "13px" }}>
                View error details
              </summary>
              <div
                style={{
                  marginTop: "8px",
                  padding: "12px",
                  backgroundColor: "var(--color-danger-border)",
                  borderRadius: "4px",
                  fontSize: "12px",
                  fontFamily: "monospace",
                }}
              >
                {preview.extraction.failureCode && (
                  <div style={{ marginBottom: "8px" }}>
                    <span style={{ fontWeight: 600 }}>Code:</span> {preview.extraction.failureCode}
                  </div>
                )}
                {preview.extraction.failureMessage && (
                  <div>
                    <span style={{ fontWeight: 600 }}>Message:</span>{" "}
                    <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                      {preview.extraction.failureMessage}
                    </pre>
                  </div>
                )}
              </div>
            </details>
          )}
          <div style={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
            <button
              onClick={onRetry}
              disabled={isLoading}
              style={{
                padding: "8px 16px",
                backgroundColor: "var(--color-danger)",
                color: "white",
                border: "none",
                borderRadius: "4px",
                cursor: isLoading ? "not-allowed" : "pointer",
                opacity: isLoading ? 0.7 : 1,
              }}
            >
              {isLoading ? "Retrying..." : "Try Again"}
            </button>
            <button
              type="button"
              onClick={() => {
                if (preview) {
                  setFormData(mapPreviewToFormData(preview));
                  setCurrentStep("editing");
                }
              }}
              style={{
                padding: "8px 16px",
                backgroundColor: "var(--color-surface)",
                color: "var(--color-text-danger-secondary)",
                border: "1px solid var(--color-danger-border)",
                borderRadius: "4px",
                cursor: "pointer",
              }}
            >
              Edit Manually
            </button>
          </div>
        </div>
      )}
      {preview?.status === "graph-error" && (
        <div
          style={{
            padding: "16px",
            backgroundColor: "var(--color-danger-bg)",
            borderRadius: "6px",
            marginBottom: "16px",
          }}
        >
          <div style={{ color: "var(--color-text-danger)", fontWeight: 600, marginBottom: "8px" }}>
            ⚠️ Graph Error
          </div>
          <p style={{ color: "var(--color-text-danger-secondary)", fontSize: "14px", margin: "0 0 12px" }}>
            The extracted graph DSL is invalid.
          </p>
          <button
            onClick={() => setCurrentStep("editing")}
            style={{
              padding: "8px 16px",
              backgroundColor: "var(--color-danger)",
              color: "white",
              border: "none",
              borderRadius: "4px",
              cursor: "pointer",
            }}
          >
            Edit Manually
          </button>
        </div>
      )}
      {error && (
        <div
          style={{
            padding: "12px 16px",
            backgroundColor: "var(--color-danger-bg)",
            borderRadius: "6px",
            color: "var(--color-text-danger)",
            fontSize: "14px",
          }}
        >
          {error}
        </div>
      )}
    </div>
  );
}

interface EditingStepProps {
  previewId: string;
  preview: IngestionPreview | null;
  formData: WizardFormData;
  onFieldChange: (field: keyof WizardFormData, value: string | string[]) => void;
  graphError: string;
  onClearGraphError: () => void;
  onGraphError: (errorMsg: string) => void;
  tagSuggestions: string[];
  error: string;
  setCurrentStep: (step: WizardStep) => void;
  onCancel: (() => void) | undefined;
}

function EditingStep({
  previewId,
  preview,
  formData,
  onFieldChange,
  graphError,
  onClearGraphError,
  onGraphError,
  tagSuggestions,
  error,
  setCurrentStep,
  onCancel,
}: EditingStepProps) {
  return (
    <div style={{ padding: "32px" }}>
      <h3 style={{ margin: "0 0 24px", fontSize: "18px", fontWeight: 600 }}>
        Edit Problem Details
      </h3>

      {previewId && preview?.sourceImage && (
        <div style={{ marginBottom: "24px" }}>
          <label
            style={{
              display: "block",
              marginBottom: "6px",
              fontSize: "14px",
              fontWeight: 500,
              color: "var(--color-text)",
            }}
          >
            Original Image
          </label>
          <img
            src={`/api/v1/ingestion-previews/${previewId}/image`}
            alt="Original problem image"
            style={{
              maxWidth: "100%",
              maxHeight: "300px",
              borderRadius: "6px",
              border: "1px solid var(--color-border)",
            }}
            data-testid="source-image"
          />
        </div>
      )}

      <div style={{ marginBottom: "24px" }}>
        <label
          style={{
            display: "block",
            marginBottom: "6px",
            fontSize: "14px",
            fontWeight: 500,
            color: "var(--color-text)",
          }}
        >
          Problem Text
        </label>
        <textarea
          value={formData.text}
          onChange={(e) => onFieldChange("text", e.target.value)}
          rows={4}
          style={{
            width: "100%",
            padding: "10px 12px",
            border: "1px solid var(--color-border-muted)",
            borderRadius: "6px",
            fontSize: "14px",
            fontFamily: "inherit",
            resize: "vertical",
            boxSizing: "border-box",
            backgroundColor: "var(--color-surface)",
            color: "var(--color-text)",
          }}
          placeholder="Enter the problem statement..."
          data-testid="text-input"
        />
      </div>

      <div style={{ marginBottom: "24px" }}>
        <label
          style={{
            display: "block",
            marginBottom: "6px",
            fontSize: "14px",
            fontWeight: 500,
            color: "var(--color-text)",
          }}
        >
          Problem Type
        </label>
        <select
          value={formData.problemType}
          onChange={(e) => onFieldChange("problemType", e.target.value)}
          style={{
            width: "100%",
            padding: "10px 12px",
            border: "1px solid var(--color-border-muted)",
            borderRadius: "6px",
            fontSize: "14px",
            fontFamily: "inherit",
            boxSizing: "border-box",
            backgroundColor: "var(--color-surface)",
            color: "var(--color-text)",
          }}
          data-testid="problem-type-input"
        >
          <option value="">Select a problem type…</option>
          <option value="single-choice">Single Choice</option>
          <option value="multi-choice">Multi Choice</option>
          <option value="fill-in-the-blank">Fill in the Blank</option>
          <option value="short-answer">Short Answer</option>
        </select>
      </div>

      <div style={{ marginBottom: "24px" }}>
        <label
          style={{
            display: "block",
            marginBottom: "6px",
            fontSize: "14px",
            fontWeight: 500,
            color: "var(--color-text)",
          }}
        >
          Graph DSL
        </label>
        <textarea
          value={formData.graphDsl}
          onChange={(e) => onFieldChange("graphDsl", e.target.value)}
          rows={6}
          style={{
            width: "100%",
            padding: "10px 12px",
            border: "1px solid var(--color-border-muted)",
            borderRadius: "6px",
            fontSize: "13px",
            fontFamily: "monospace",
            resize: "vertical",
            boxSizing: "border-box",
            backgroundColor: "var(--color-surface)",
            color: "var(--color-text)",
          }}
          placeholder="Enter JSXGraph DSL code..."
          data-testid="graph-dsl-input"
        />

        {formData.graphDsl && (
          <div style={{ marginTop: "16px" }}>
            <div
              style={{
                fontSize: "14px",
                fontWeight: 500,
                color: "var(--color-text)",
                marginBottom: "8px",
              }}
            >
              Graph Preview
            </div>
            <GraphSandbox
              dsl={formData.graphDsl}
              height={300}
              onError={onGraphError}
              onRender={onClearGraphError}
            />
            {graphError && (
              <div style={{ marginTop: "8px", color: "var(--color-text-danger)", fontSize: "14px" }}>
                {graphError}
              </div>
            )}
          </div>
        )}
      </div>

      <div style={{ marginBottom: "24px" }}>
        <label
          style={{
            display: "block",
            marginBottom: "6px",
            fontSize: "14px",
            fontWeight: 500,
            color: "var(--color-text)",
          }}
        >
          Correct Answer
        </label>
        <input
          type="text"
          value={formData.correctAnswer}
          onChange={(e) => onFieldChange("correctAnswer", e.target.value)}
          style={{
            width: "100%",
            padding: "10px 12px",
            border: "1px solid var(--color-border-muted)",
            borderRadius: "6px",
            fontSize: "14px",
            fontFamily: "inherit",
            boxSizing: "border-box",
            backgroundColor: "var(--color-surface)",
            color: "var(--color-text)",
          }}
          placeholder="Enter the correct answer..."
          data-testid="correct-answer-input"
        />
      </div>

      <div style={{ marginBottom: "24px" }}>
        <TagInput
          tags={formData.tags}
          onChange={(tags) => onFieldChange("tags", tags)}
          suggestions={tagSuggestions}
          placeholder="Add a tag..."
          testId="tags-input"
        />
      </div>

      {error && (
        <div
          style={{
            padding: "12px 16px",
            backgroundColor: "var(--color-danger-bg)",
            borderRadius: "6px",
            color: "var(--color-text-danger)",
            fontSize: "14px",
            marginBottom: "16px",
          }}
        >
          {error}
        </div>
      )}

      <div style={{ display: "flex", gap: "12px" }}>
        <button
          onClick={() => setCurrentStep("confirming")}
          style={{
            padding: "10px 20px",
            backgroundColor: "var(--color-primary)",
            color: "white",
            border: "none",
            borderRadius: "6px",
            cursor: "pointer",
            fontSize: "14px",
            fontWeight: 500,
          }}
          data-testid="review-button"
        >
          Review & Confirm
        </button>
        <button
          onClick={onCancel}
          style={{
            padding: "10px 20px",
            backgroundColor: "var(--color-surface)",
            color: "var(--color-text)",
            border: "1px solid var(--color-border-muted)",
            borderRadius: "6px",
            cursor: "pointer",
            fontSize: "14px",
            fontWeight: 500,
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}

interface ConfirmingStepProps {
  formData: WizardFormData;
  graphError: string;
  onGraphError: (errorMsg: string) => void;
  error: string;
  isLoading: boolean;
  onConfirm: () => void;
  setCurrentStep: (step: WizardStep) => void;
}

function ConfirmingStep({
  formData,
  graphError,
  onGraphError,
  error,
  isLoading,
  onConfirm,
  setCurrentStep,
}: ConfirmingStepProps) {
  return (
    <div style={{ padding: "32px" }}>
      <h3 style={{ margin: "0 0 24px", fontSize: "18px", fontWeight: 600 }}>
        Confirm Problem
      </h3>

      <div
        style={{
          backgroundColor: "var(--color-surface-muted)",
          borderRadius: "8px",
          padding: "24px",
          marginBottom: "24px",
        }}
      >
        <div style={{ marginBottom: "16px" }}>
          <div
            style={{
              fontSize: "12px",
              fontWeight: 600,
              color: "var(--color-text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              marginBottom: "4px",
            }}
          >
            Problem Text
          </div>
          <LatexText
            text={formData.text || "(empty)"}
            style={{ fontSize: "14px", color: "var(--color-text)" }}
          />
        </div>

        <div style={{ marginBottom: "16px" }}>
          <div
            style={{
              fontSize: "12px",
              fontWeight: 600,
              color: "var(--color-text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              marginBottom: "4px",
            }}
          >
            Type
          </div>
          <div style={{ fontSize: "14px", color: "var(--color-text)" }}>
            {formData.problemType || "(empty)"}
          </div>
        </div>

        <div style={{ marginBottom: "16px" }}>
          <div
            style={{
              fontSize: "12px",
              fontWeight: 600,
              color: "var(--color-text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              marginBottom: "4px",
            }}
          >
            Graph DSL
          </div>
          <div
            style={{
              fontSize: "13px",
              color: "var(--color-text)",
              fontFamily: "monospace",
              backgroundColor: "var(--color-surface)",
              padding: "8px",
              borderRadius: "4px",
              border: "1px solid var(--color-border)",
            }}
          >
            {formData.graphDsl || "(empty)"}
          </div>
        </div>

        <div style={{ marginBottom: "16px" }}>
          <div
            style={{
              fontSize: "12px",
              fontWeight: 600,
              color: "var(--color-text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              marginBottom: "4px",
            }}
          >
            Correct Answer
          </div>
          <div style={{ fontSize: "14px", color: "var(--color-text)" }}>
            {formData.correctAnswer || "(empty)"}
          </div>
        </div>

        <div>
          <div
            style={{
              fontSize: "12px",
              fontWeight: 600,
              color: "var(--color-text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              marginBottom: "4px",
            }}
          >
            Tags
          </div>
          <div style={{ fontSize: "14px", color: "var(--color-text)" }}>
            {formData.tags.length > 0 ? formData.tags.join(", ") : "(empty)"}
          </div>
        </div>
      </div>

      {formData.graphDsl && (
        <div style={{ marginBottom: "24px" }}>
          <div
            style={{
              fontSize: "14px",
              fontWeight: 500,
              color: "var(--color-text)",
              marginBottom: "8px",
            }}
          >
            Graph Preview
          </div>
          <GraphSandbox
            dsl={formData.graphDsl}
            height={300}
            onError={onGraphError}
          />
          {graphError && (
            <div style={{ marginTop: "8px", color: "var(--color-text-danger)", fontSize: "14px" }}>
              {graphError}
            </div>
          )}
        </div>
      )}

      {error && (
        <div
          style={{
            padding: "12px 16px",
            backgroundColor: "var(--color-danger-bg)",
            borderRadius: "6px",
            color: "var(--color-text-danger)",
            fontSize: "14px",
            marginBottom: "16px",
          }}
        >
          {error}
        </div>
      )}

      <div style={{ display: "flex", gap: "12px" }}>
        <button
          onClick={onConfirm}
          disabled={isLoading}
          style={{
            padding: "10px 20px",
            backgroundColor: "var(--color-success)",
            color: "white",
            border: "none",
            borderRadius: "6px",
            cursor: isLoading ? "not-allowed" : "pointer",
            fontSize: "14px",
            fontWeight: 500,
            opacity: isLoading ? 0.7 : 1,
          }}
          data-testid="confirm-button"
        >
          {isLoading ? "Creating..." : "Confirm & Save"}
        </button>
        <button
          onClick={() => setCurrentStep("editing")}
          disabled={isLoading}
          style={{
            padding: "10px 20px",
            backgroundColor: "var(--color-surface)",
            color: "var(--color-text)",
            border: "1px solid var(--color-border-muted)",
            borderRadius: "6px",
            cursor: isLoading ? "not-allowed" : "pointer",
            fontSize: "14px",
            fontWeight: 500,
          }}
        >
          Back to Edit
        </button>
      </div>
    </div>
  );
}

export function IngestionWizard({ onConfirm, onCancel }: IngestionWizardProps) {
  const [currentStep, setCurrentStep] = useState<WizardStep>("paste");
  const [previewId, setPreviewId] = useState<string>("");
  const [preview, setPreview] = useState<IngestionPreview | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string>("");
  const [uploadProgress, setUploadProgress] = useState(0);

  const [formData, setFormData] = useState<WizardFormData>({
    text: "",
    problemType: "",
    graphDsl: "",
    correctAnswer: "",
    tags: [],
  });

  const [graphError, setGraphError] = useState<string>("");
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const tagSuggestions = useTagSuggestions();

  useEffect(() => {
    const savedDraft = localStorage.getItem("ingestion-draft");
    if (savedDraft) {
      try {
        const draft = JSON.parse(savedDraft) as Partial<typeof formData>;
        setFormData({
          text: draft.text || "",
          problemType: draft.problemType || "",
          graphDsl: draft.graphDsl || "",
          correctAnswer: draft.correctAnswer || "",
          tags: Array.isArray(draft.tags) ? draft.tags : [],
        });
      } catch {
      }
    }
  }, []);

  const saveDraft = useCallback(() => {
    localStorage.setItem("ingestion-draft", JSON.stringify(formData));
  }, [formData]);

  const debouncedSaveDraft = useDebouncedCallback(saveDraft, 1000);

  useEffect(() => {
    if (currentStep === "editing" || currentStep === "preview") {
      debouncedSaveDraft();
    }
  }, [formData, currentStep, debouncedSaveDraft]);

  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const startPolling = useCallback((id: string) => {
    if (pollIntervalRef.current) {
      clearInterval(pollIntervalRef.current);
    }

    pollIntervalRef.current = setInterval(async () => {
      try {
        const updated = await getPreview(id);
        setPreview(updated);

        if (updated.status === "ready") {
          setFormData(mapPreviewToFormData(updated));
          setCurrentStep("editing");
          if (pollIntervalRef.current) {
            clearInterval(pollIntervalRef.current);
            pollIntervalRef.current = null;
          }
        } else if (updated.status === "vlm-failed" || updated.status === "graph-error") {
          if (pollIntervalRef.current) {
            clearInterval(pollIntervalRef.current);
            pollIntervalRef.current = null;
          }
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : "Failed to fetch preview");
        if (pollIntervalRef.current) {
          clearInterval(pollIntervalRef.current);
          pollIntervalRef.current = null;
        }
      }
    }, 2000);
  }, []);

  useEffect(() => {
    return () => {
      if (pollIntervalRef.current) {
        clearInterval(pollIntervalRef.current);
      }
    };
  }, []);

  const processImageFile = useCallback(
    async (imageFile: File) => {
      setError("");
      setCurrentStep("uploading");
      setUploadProgress(0);

      const progressInterval = setInterval(() => {
        setUploadProgress((prev) => Math.min(prev + 10, 90));
      }, 100);

      try {
        const result = await createPreview(imageFile);
        setPreviewId(result.id);
        setPreview(result);
        setUploadProgress(100);
        clearInterval(progressInterval);

        if (result.status === "extracting") {
          setCurrentStep("preview");
          startPolling(result.id);
        } else if (result.status === "ready") {
          setFormData(mapPreviewToFormData(result));
          setCurrentStep("editing");
        } else {
          setCurrentStep("preview");
        }
      } catch (err) {
        clearInterval(progressInterval);
        setError(err instanceof Error ? err.message : "Failed to upload image");
        setCurrentStep("paste");
      }
    },
    [startPolling]
  );

  const handlePaste = useCallback(
    async (event: ClipboardEvent) => {
      event.preventDefault();

      const items = event.clipboardData.items;
      let imageFile: File | null = null;

      for (const item of items) {
        if (item.type.startsWith("image/")) {
          const file = item.getAsFile();
          if (file) {
            imageFile = file;
            break;
          }
        }
      }

      if (!imageFile) {
        setError("No image found in clipboard. Please copy an image first.");
        return;
      }

      await processImageFile(imageFile);
    },
    [processImageFile]
  );

  const handleFileSelection = useCallback(
    async (event: ChangeEvent<HTMLInputElement>) => {
      const imageFile = event.target.files?.[0];
      event.target.value = "";
      if (!imageFile) {
        return;
      }

      await processImageFile(imageFile);
    },
    [processImageFile]
  );

  const handleRetry = useCallback(async () => {
    if (!previewId) return;

    setIsLoading(true);
    setError("");

    try {
      const result = await retryPreview(previewId);
      setPreview(result);

      if (result.status === "extracting") {
        setCurrentStep("preview");
        startPolling(previewId);
      } else if (result.status === "ready") {
        setFormData(mapPreviewToFormData(result));
        setCurrentStep("editing");
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to retry");
    } finally {
      setIsLoading(false);
    }
  }, [previewId, startPolling]);

  const handleFieldChange = useCallback(
    (field: keyof typeof formData, value: string | string[]) => {
      setFormData((prev) => ({ ...prev, [field]: value }));
    },
    []
  );

  const handleConfirm = useCallback(async () => {
    if (!previewId) return;

    setIsLoading(true);
    setError("");

    try {
      await updatePreview(previewId, {
        text: formData.text,
        problemType: formData.problemType,
        graphDsl: formData.graphDsl,
        correctAnswer: formData.correctAnswer,
        tags: formData.tags,
      });

      const result = await confirmPreview(previewId);

      localStorage.removeItem("ingestion-draft");

      onConfirm?.(result.problemId);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to confirm");
    } finally {
      setIsLoading(false);
    }
  }, [previewId, formData, onConfirm]);

  const handleGraphError = useCallback((errorMsg: string) => {
    setGraphError(errorMsg);
  }, []);

  const renderStepContent = () => {
    switch (currentStep) {
      case "paste":
        return (
          <PasteStep
            onPaste={handlePaste}
            fileInputRef={fileInputRef}
            onFileSelection={handleFileSelection}
            error={error}
          />
        );
      case "uploading":
        return <UploadingStep uploadProgress={uploadProgress} />;
      case "preview":
        return (
          <PreviewStep
            preview={preview}
            isLoading={isLoading}
            onRetry={handleRetry}
            setFormData={setFormData}
            setCurrentStep={setCurrentStep}
            error={error}
          />
        );
      case "editing":
        return (
          <EditingStep
            previewId={previewId}
            preview={preview}
            formData={formData}
            onFieldChange={handleFieldChange}
            graphError={graphError}
            onClearGraphError={() => setGraphError("")}
            onGraphError={handleGraphError}
            tagSuggestions={tagSuggestions}
            error={error}
            setCurrentStep={setCurrentStep}
            onCancel={onCancel}
          />
        );
      case "confirming":
        return (
          <ConfirmingStep
            formData={formData}
            graphError={graphError}
            onGraphError={handleGraphError}
            error={error}
            isLoading={isLoading}
            onConfirm={handleConfirm}
            setCurrentStep={setCurrentStep}
          />
        );
      default:
        return null;
    }
  };

  return (
    <div
      style={{
        maxWidth: "800px",
        margin: "0 auto",
        padding: "24px",
      }}
      data-testid="ingestion-wizard"
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
        {[
          { key: "paste", label: "Paste" },
          { key: "uploading", label: "Upload" },
          { key: "preview", label: "Process" },
          { key: "editing", label: "Edit" },
          { key: "confirming", label: "Confirm" },
        ].map((step, index, arr) => {
          const isActive =
            currentStep === step.key ||
            (step.key === "paste" && currentStep === "paste") ||
            (step.key === "uploading" &&
              ["uploading", "preview", "editing", "confirming"].includes(currentStep)) ||
            (step.key === "preview" &&
              ["preview", "editing", "confirming"].includes(currentStep)) ||
            (step.key === "editing" && ["editing", "confirming"].includes(currentStep)) ||
            (step.key === "confirming" && currentStep === "confirming");

          return (
            <div key={step.key} style={{ display: "flex", alignItems: "center" }}>
              <div
                style={{
                  padding: "8px 16px",
                  borderRadius: "20px",
                  fontSize: "13px",
                  fontWeight: 500,
                  backgroundColor: isActive ? "var(--color-primary)" : "var(--color-border)",
                  color: isActive ? "white" : "var(--color-text-muted)",
                }}
              >
                {step.label}
              </div>
              {index < arr.length - 1 && (
                <div
                  style={{
                    width: "24px",
                    height: "2px",
                    backgroundColor: isActive ? "var(--color-primary)" : "var(--color-border)",
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
        }}
      >
        {renderStepContent()}
      </div>

      <style>{`
        @keyframes spin {
          to {
            transform: rotate(360deg);
          }
        }
      `}</style>
    </div>
  );
}
