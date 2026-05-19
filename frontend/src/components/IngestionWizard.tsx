import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, ClipboardEvent } from "react";
import { GraphSandbox } from "./GraphSandbox";
import { TagInput } from "./TagInput";
import api from "@/api/client";
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

export function IngestionWizard({ onConfirm, onCancel }: IngestionWizardProps) {
  const [currentStep, setCurrentStep] = useState<WizardStep>("paste");
  const [previewId, setPreviewId] = useState<string>("");
  const [preview, setPreview] = useState<IngestionPreview | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string>("");
  const [uploadProgress, setUploadProgress] = useState(0);

  const [formData, setFormData] = useState<{
    text: string;
    problemType: string;
    graphDsl: string;
    correctAnswer: string;
    tags: string[];
  }>({
    text: "",
    problemType: "",
    graphDsl: "",
    correctAnswer: "",
    tags: [] as string[],
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
          <div
            style={{
              padding: "48px 32px",
              textAlign: "center",
              border: "2px dashed #d1d5db",
              borderRadius: "8px",
              backgroundColor: "#f9fafb",
            }}
            onPaste={handlePaste}
            tabIndex={0}
            role="region"
            aria-label="Paste image area"
          >
            <div style={{ marginBottom: "16px", fontSize: "48px" }}>📋</div>
            <h3 style={{ margin: "0 0 8px", fontSize: "18px", fontWeight: 600 }}>
              Paste an Image
            </h3>
            <p style={{ margin: 0, color: "#6b7280", fontSize: "14px" }}>
              Copy an image and paste it here (Ctrl+V or Cmd+V)
            </p>
            <div style={{ marginTop: "16px" }}>
              <button
                type="button"
                onClick={() => fileInputRef.current?.click()}
                style={{
                  padding: "8px 16px",
                  backgroundColor: "white",
                  color: "#374151",
                  border: "1px solid #d1d5db",
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
                onChange={(e) => void handleFileSelection(e)}
                style={{ display: "none" }}
              />
            </div>
            {error && (
              <div
                style={{
                  marginTop: "16px",
                  padding: "12px 16px",
                  backgroundColor: "#fef2f2",
                  borderRadius: "6px",
                  color: "#dc2626",
                  fontSize: "14px",
                }}
              >
                {error}
              </div>
            )}
          </div>
        );

      case "uploading":
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
                backgroundColor: "#e5e7eb",
                borderRadius: "4px",
                overflow: "hidden",
                marginTop: "16px",
              }}
            >
              <div
                style={{
                  width: `${uploadProgress}%`,
                  height: "100%",
                  backgroundColor: "#3b82f6",
                  transition: "width 0.3s ease",
                }}
              />
            </div>
            <p style={{ margin: "8px 0 0", color: "#6b7280", fontSize: "14px" }}>
              {uploadProgress}%
            </p>
          </div>
        );

      case "preview":
        return (
          <div style={{ padding: "32px" }}>
            <h3 style={{ margin: "0 0 16px", fontSize: "18px", fontWeight: 600 }}>
              Processing Image
            </h3>
            {preview?.status === "extracting" && (
              <div style={{ textAlign: "center", padding: "32px" }}>
                <div style={{ fontSize: "32px", marginBottom: "16px" }}>🤖</div>
                <p style={{ color: "#6b7280" }}>
                  AI is analyzing the image and extracting problem data...
                </p>
                <div
                  style={{
                    width: "40px",
                    height: "40px",
                    border: "3px solid #e5e7eb",
                    borderTopColor: "#3b82f6",
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
                  backgroundColor: "#fef2f2",
                  borderRadius: "6px",
                  marginBottom: "16px",
                }}
              >
                <div style={{ color: "#dc2626", fontWeight: 600, marginBottom: "8px" }}>
                  ⚠️ Extraction Failed
                </div>
                <p style={{ color: "#7f1d1d", fontSize: "14px", margin: "0 0 12px" }}>
                  The AI was unable to extract problem data from the image.
                </p>
                <div style={{ display: "flex", gap: "12px", flexWrap: "wrap" }}>
                  <button
                    onClick={handleRetry}
                    disabled={isLoading}
                    style={{
                      padding: "8px 16px",
                      backgroundColor: "#dc2626",
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
                      backgroundColor: "white",
                      color: "#7f1d1d",
                      border: "1px solid #fca5a5",
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
                  backgroundColor: "#fef2f2",
                  borderRadius: "6px",
                  marginBottom: "16px",
                }}
              >
                <div style={{ color: "#dc2626", fontWeight: 600, marginBottom: "8px" }}>
                  ⚠️ Graph Error
                </div>
                <p style={{ color: "#7f1d1d", fontSize: "14px", margin: "0 0 12px" }}>
                  The extracted graph DSL is invalid.
                </p>
                <button
                  onClick={() => setCurrentStep("editing")}
                  style={{
                    padding: "8px 16px",
                    backgroundColor: "#dc2626",
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
                  backgroundColor: "#fef2f2",
                  borderRadius: "6px",
                  color: "#dc2626",
                  fontSize: "14px",
                }}
              >
                {error}
              </div>
            )}
          </div>
        );

      case "editing":
        return (
          <div style={{ padding: "32px" }}>
            <h3 style={{ margin: "0 0 24px", fontSize: "18px", fontWeight: 600 }}>
              Edit Problem Details
            </h3>

            <div style={{ marginBottom: "24px" }}>
              <label
                style={{
                  display: "block",
                  marginBottom: "6px",
                  fontSize: "14px",
                  fontWeight: 500,
                  color: "#374151",
                }}
              >
                Problem Text
              </label>
              <textarea
                value={formData.text}
                onChange={(e) => handleFieldChange("text", e.target.value)}
                rows={4}
                style={{
                  width: "100%",
                  padding: "10px 12px",
                  border: "1px solid #d1d5db",
                  borderRadius: "6px",
                  fontSize: "14px",
                  fontFamily: "inherit",
                  resize: "vertical",
                  boxSizing: "border-box",
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
                  color: "#374151",
                }}
              >
                Problem Type
              </label>
              <select
                value={formData.problemType}
                onChange={(e) => handleFieldChange("problemType", e.target.value)}
                style={{
                  width: "100%",
                  padding: "10px 12px",
                  border: "1px solid #d1d5db",
                  borderRadius: "6px",
                  fontSize: "14px",
                  fontFamily: "inherit",
                  boxSizing: "border-box",
                  backgroundColor: "white",
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
                  color: "#374151",
                }}
              >
                Graph DSL
              </label>
              <textarea
                value={formData.graphDsl}
                onChange={(e) => handleFieldChange("graphDsl", e.target.value)}
                rows={6}
                style={{
                  width: "100%",
                  padding: "10px 12px",
                  border: "1px solid #d1d5db",
                  borderRadius: "6px",
                  fontSize: "13px",
                  fontFamily: "monospace",
                  resize: "vertical",
                  boxSizing: "border-box",
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
                      color: "#374151",
                      marginBottom: "8px",
                    }}
                  >
                    Graph Preview
                  </div>
                  <GraphSandbox
                    dsl={formData.graphDsl}
                    height={300}
                    onError={handleGraphError}
                    onRender={() => setGraphError("")}
                  />
                  {graphError && (
                    <div style={{ marginTop: "8px", color: "#dc2626", fontSize: "14px" }}>
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
                  color: "#374151",
                }}
              >
                Correct Answer
              </label>
              <input
                type="text"
                value={formData.correctAnswer}
                onChange={(e) => handleFieldChange("correctAnswer", e.target.value)}
                style={{
                  width: "100%",
                  padding: "10px 12px",
                  border: "1px solid #d1d5db",
                  borderRadius: "6px",
                  fontSize: "14px",
                  fontFamily: "inherit",
                  boxSizing: "border-box",
                }}
                placeholder="Enter the correct answer..."
                data-testid="correct-answer-input"
              />
            </div>

            <div style={{ marginBottom: "24px" }}>
              <TagInput
                tags={formData.tags}
                onChange={(tags) => handleFieldChange("tags", tags)}
                suggestions={tagSuggestions}
                placeholder="Add a tag..."
                testId="tags-input"
              />
            </div>

            {error && (
              <div
                style={{
                  padding: "12px 16px",
                  backgroundColor: "#fef2f2",
                  borderRadius: "6px",
                  color: "#dc2626",
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
                  backgroundColor: "#3b82f6",
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
                  backgroundColor: "white",
                  color: "#374151",
                  border: "1px solid #d1d5db",
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

      case "confirming":
        return (
          <div style={{ padding: "32px" }}>
            <h3 style={{ margin: "0 0 24px", fontSize: "18px", fontWeight: 600 }}>
              Confirm Problem
            </h3>

            <div
              style={{
                backgroundColor: "#f9fafb",
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
                    color: "#6b7280",
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                    marginBottom: "4px",
                  }}
                >
                  Problem Text
                </div>
                <div style={{ fontSize: "14px", color: "#111827" }}>
                  {formData.text || "(empty)"}
                </div>
              </div>

              <div style={{ marginBottom: "16px" }}>
                <div
                  style={{
                    fontSize: "12px",
                    fontWeight: 600,
                    color: "#6b7280",
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                    marginBottom: "4px",
                  }}
                >
                  Type
                </div>
                <div style={{ fontSize: "14px", color: "#111827" }}>
                  {formData.problemType || "(empty)"}
                </div>
              </div>

              <div style={{ marginBottom: "16px" }}>
                <div
                  style={{
                    fontSize: "12px",
                    fontWeight: 600,
                    color: "#6b7280",
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
                    color: "#111827",
                    fontFamily: "monospace",
                    backgroundColor: "white",
                    padding: "8px",
                    borderRadius: "4px",
                    border: "1px solid #e5e7eb",
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
                    color: "#6b7280",
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                    marginBottom: "4px",
                  }}
                >
                  Correct Answer
                </div>
                <div style={{ fontSize: "14px", color: "#111827" }}>
                  {formData.correctAnswer || "(empty)"}
                </div>
              </div>

              <div>
                <div
                  style={{
                    fontSize: "12px",
                    fontWeight: 600,
                    color: "#6b7280",
                    textTransform: "uppercase",
                    letterSpacing: "0.05em",
                    marginBottom: "4px",
                  }}
                >
                  Tags
                </div>
                <div style={{ fontSize: "14px", color: "#111827" }}>
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
                    color: "#374151",
                    marginBottom: "8px",
                  }}
                >
                  Graph Preview
                </div>
                <GraphSandbox
                  dsl={formData.graphDsl}
                  height={300}
                  onError={handleGraphError}
                />
                {graphError && (
                  <div style={{ marginTop: "8px", color: "#dc2626", fontSize: "14px" }}>
                    {graphError}
                  </div>
                )}
              </div>
            )}

            {error && (
              <div
                style={{
                  padding: "12px 16px",
                  backgroundColor: "#fef2f2",
                  borderRadius: "6px",
                  color: "#dc2626",
                  fontSize: "14px",
                  marginBottom: "16px",
                }}
              >
                {error}
              </div>
            )}

            <div style={{ display: "flex", gap: "12px" }}>
              <button
                onClick={handleConfirm}
                disabled={isLoading}
                style={{
                  padding: "10px 20px",
                  backgroundColor: "#10b981",
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
                  backgroundColor: "white",
                  color: "#374151",
                  border: "1px solid #d1d5db",
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
                  backgroundColor: isActive ? "#3b82f6" : "#e5e7eb",
                  color: isActive ? "white" : "#6b7280",
                }}
              >
                {step.label}
              </div>
              {index < arr.length - 1 && (
                <div
                  style={{
                    width: "24px",
                    height: "2px",
                    backgroundColor: isActive ? "#3b82f6" : "#e5e7eb",
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
          backgroundColor: "white",
          borderRadius: "12px",
          boxShadow: "0 1px 3px rgba(0, 0, 0, 0.1)",
          border: "1px solid #e5e7eb",
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
