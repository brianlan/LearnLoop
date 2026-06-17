import type { IngestionPreview, WizardFormData, WizardStep } from "../IngestionWizard";
import { mapPreviewToFormData } from "./mapPreviewToFormData";

export interface PreviewStepProps {
  preview: IngestionPreview | null;
  isLoading: boolean;
  onRetry: () => void;
  setFormData: React.Dispatch<React.SetStateAction<WizardFormData>>;
  setCurrentStep: (step: WizardStep) => void;
  error: string;
  helperFailureSubject: string;
  onHelperFailureSubjectChange: (subject: string) => void;
}

export function PreviewStep({
  preview,
  isLoading,
  onRetry,
  setFormData,
  setCurrentStep,
  error,
  helperFailureSubject,
  onHelperFailureSubjectChange,
}: PreviewStepProps) {
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
      {preview?.status === "vlm-failed" && (() => {
        const helperDetection = preview?.helperDetection;
        const isHelperFailure = helperDetection?.failureCode;
        const failureCode = isHelperFailure
          ? helperDetection.failureCode
          : preview?.extraction?.failureCode;
        const failureMessage = isHelperFailure
          ? helperDetection.failureMessage
          : preview?.extraction?.failureMessage;
        return (
        <div
          style={{
            padding: "16px",
            backgroundColor: "var(--color-danger-bg)",
            borderRadius: "6px",
            marginBottom: "16px",
          }}
        >
          <div style={{ color: "var(--color-text-danger)", fontWeight: 600, marginBottom: "8px" }}>
            ⚠️ {isHelperFailure ? "Subject Classification Failed" : "Extraction Failed"}
          </div>
          <p style={{ color: "var(--color-text-danger-secondary)", fontSize: "14px", margin: "0 0 12px" }}>
            {isHelperFailure
              ? "The AI could not determine the subject (math or English). Please select the subject manually and retry."
              : "The AI was unable to extract problem data from the image."}
          </p>
          {(failureCode || failureMessage) && (
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
                {failureCode && (
                  <div style={{ marginBottom: "8px" }}>
                    <span style={{ fontWeight: 600 }}>Code:</span> {failureCode}
                  </div>
                )}
                {failureMessage && (
                  <div>
                    <span style={{ fontWeight: 600 }}>Message:</span>{" "}
                    <pre style={{ margin: 0, whiteSpace: "pre-wrap", wordBreak: "break-word" }}>
                      {failureMessage}
                    </pre>
                  </div>
                )}
              </div>
            </details>
          )}
          {isHelperFailure && (
            <div style={{ marginBottom: "12px" }}>
              <label
                htmlFor="helper-failure-subject"
                style={{ display: "block", marginBottom: "4px", fontSize: "14px", color: "var(--color-text)" }}
              >
                Subject
              </label>
              <select
                id="helper-failure-subject"
                data-testid="helper-failure-subject-select"
                value={helperFailureSubject}
                onChange={(e) => onHelperFailureSubjectChange(e.target.value)}
                disabled={isLoading}
                style={{
                  padding: "8px 12px",
                  border: "1px solid var(--color-border)",
                  borderRadius: "4px",
                  backgroundColor: "var(--color-surface)",
                  color: "var(--color-text)",
                  fontSize: "14px",
                }}
              >
                <option value="math">Math</option>
                <option value="english">English</option>
              </select>
            </div>
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
        );
      })()}
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
