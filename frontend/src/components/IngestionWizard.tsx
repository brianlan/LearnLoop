import { useCallback, useEffect, useRef, useState } from "react";
import type { ChangeEvent, ClipboardEvent } from "react";
import { api } from "@/api/client";
import { useTagSuggestions } from "@/hooks/useTagSuggestions";
import { PasteStep } from "./ingestion/PasteStep";
import { UploadingStep } from "./ingestion/UploadingStep";
import { PreviewStep } from "./ingestion/PreviewStep";
import { EditingStep } from "./ingestion/EditingStep";
import { ConfirmingStep } from "./ingestion/ConfirmingStep";
import { mapPreviewToFormData } from "./ingestion/mapPreviewToFormData";

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
  subject: string;
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

interface PreviewHelperDetection {
  subject?: string | null;
  confidence?: number | null;
  reason?: string | null;
  model?: string | null;
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
  helperDetection?: PreviewHelperDetection;
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
      subject: "math",
    },
    extraction: candidate.extraction ?? {},
    createdAt: candidate.createdAt ?? "",
    updatedAt: candidate.updatedAt ?? "",
    expiresAt: candidate.expiresAt ?? "",
  };
}

export type WizardStep = "paste" | "uploading" | "preview" | "editing" | "confirming";

export interface WizardFormData {
  text: string;
  problemType: string;
  graphDsl: string;
  correctAnswer: string;
  tags: string[];
  subject: string;
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
  subject?: string;
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

  const [formData, setFormData] = useState<WizardFormData>({
    text: "",
    problemType: "",
    graphDsl: "",
    correctAnswer: "",
    tags: [],
    subject: "math",
  });

  const [graphError, setGraphError] = useState<string>("");
  const [helperFailureSubject, setHelperFailureSubject] = useState<string>("math");
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
          subject: draft.subject || "math",
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
      const isHelperFailure = preview?.helperDetection?.failureCode;
      if (isHelperFailure) {
        await updatePreview(previewId, { subject: helperFailureSubject });
      }

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
  }, [previewId, preview, helperFailureSubject, startPolling]);

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
        subject: formData.subject,
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
            helperFailureSubject={helperFailureSubject}
            onHelperFailureSubjectChange={setHelperFailureSubject}
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
