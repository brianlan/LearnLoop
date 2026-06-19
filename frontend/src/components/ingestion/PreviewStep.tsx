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
    <div style={{ padding: "2rem" }}>
      <h3 style={{ margin: "0 0 1.25rem", fontSize: "1.25rem", fontWeight: 700 }}>
        Processing Image
      </h3>
      {preview?.status === "extracting" && (
        <div style={{ textAlign: "center", padding: "2rem" }}>
          <div style={{ fontSize: "2.5rem", marginBottom: "1rem" }}>🤖</div>
          <p style={{ color: "var(--color-text-muted)", fontWeight: 500, fontSize: "0.95rem" }}>
            AI is analyzing the image and extracting problem data...
          </p>
          <div
            style={{
              width: "40px",
              height: "40px",
              border: "3px solid var(--color-border)",
              borderTopColor: "var(--color-primary)",
              borderRadius: "50%",
              margin: "1.5rem auto",
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
            padding: "1.25rem",
            backgroundColor: "var(--color-danger-bg)",
            borderRadius: "var(--radius-md)",
            border: "1px solid var(--color-danger-border)",
            marginBottom: "1.25rem",
          }}
        >
          <div style={{ color: "var(--color-text-danger)", fontWeight: 700, marginBottom: "0.5rem", fontSize: "0.95rem" }}>
            ⚠️ {isHelperFailure ? "Subject Classification Failed" : "Extraction Failed"}
          </div>
          <p style={{ color: "var(--color-text-danger-secondary)", fontSize: "0.875rem", margin: "0 0 1rem", lineHeight: "1.4" }}>
            {isHelperFailure
              ? "The AI could not determine the subject (math or English). Please select the subject manually and retry."
              : "The AI was unable to extract problem data from the image."}
          </p>
          {(failureCode || failureMessage) && (
            <details style={{ marginBottom: "1rem" }}>
              <summary style={{ cursor: "pointer", color: "var(--color-text-danger)", fontSize: "0.8125rem", fontWeight: 600 }}>
                View error details
              </summary>
              <div
                style={{
                  marginTop: "0.5rem",
                  padding: "0.75rem",
                  backgroundColor: "var(--color-danger-border)",
                  borderRadius: "var(--radius-sm)",
                  fontSize: "0.75rem",
                  fontFamily: "monospace",
                  color: "var(--color-text-danger-secondary)",
                }}
              >
                {failureCode && (
                  <div style={{ marginBottom: "0.5rem" }}>
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
            <div style={{ marginBottom: "1.25rem" }}>
              <label
                htmlFor="helper-failure-subject"
                style={{ display: "block", marginBottom: "0.375rem", fontSize: "0.8125rem", fontWeight: 700, textTransform: "uppercase", letterSpacing: "0.05em", color: "var(--color-text-muted)" }}
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
                  padding: "0.5rem 0.75rem",
                  border: "1px solid var(--color-border)",
                  borderRadius: "var(--radius-md)",
                  backgroundColor: "var(--color-bg)",
                  color: "var(--color-text)",
                  fontSize: "0.875rem",
                  width: "100%",
                  maxWidth: "200px",
                }}
              >
                <option value="math">Math</option>
                <option value="english">English</option>
              </select>
            </div>
          )}
          <div style={{ display: "flex", gap: "0.75rem", flexWrap: "wrap" }}>
            <button
              onClick={onRetry}
              disabled={isLoading}
              className="btn btn-danger"
              style={{
                padding: "0.5rem 1.25rem",
                fontSize: "0.875rem",
                fontWeight: 600,
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
              className="btn btn-secondary"
              style={{
                padding: "0.5rem 1.25rem",
                fontSize: "0.875rem",
                fontWeight: 600,
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
            padding: "1.25rem",
            backgroundColor: "var(--color-danger-bg)",
            borderRadius: "var(--radius-md)",
            border: "1px solid var(--color-danger-border)",
            marginBottom: "1.25rem",
          }}
        >
          <div style={{ color: "var(--color-text-danger)", fontWeight: 700, marginBottom: "0.5rem", fontSize: "0.95rem" }}>
            ⚠️ Graph Error
          </div>
          <p style={{ color: "var(--color-text-danger-secondary)", fontSize: "0.875rem", margin: "0 0 1rem" }}>
            The extracted graph DSL is invalid.
          </p>
          <button
            onClick={() => setCurrentStep("editing")}
            className="btn btn-danger"
            style={{
              padding: "0.5rem 1.25rem",
              fontSize: "0.875rem",
              fontWeight: 600,
            }}
          >
            Edit Manually
          </button>
        </div>
      )}
      {error && (
        <div
          className="badge badge-danger"
          style={{
            padding: "0.75rem 1rem",
            fontSize: "0.875rem",
            textTransform: "none",
            letterSpacing: "normal",
            fontWeight: 500,
            display: "block"
          }}
        >
          {error}
        </div>
      )}
    </div>
  );
}
