import { GraphSandbox } from "../GraphSandbox";
import { LatexText } from "../LatexText";
import { parseOptions } from "../AnswerInput";
import type { WizardFormData, WizardStep } from "../IngestionWizard";

export interface ConfirmingStepProps {
  formData: WizardFormData;
  graphError: string;
  onGraphError: (errorMsg: string) => void;
  error: string;
  isLoading: boolean;
  onConfirm: () => void;
  setCurrentStep: (step: WizardStep) => void;
}

export function ConfirmingStep({
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
            Subject
          </div>
          <div style={{ fontSize: "14px", color: "var(--color-text)" }}>
            {formData.subject || "(empty)"}
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

        {(formData.problemType === "single-choice" || formData.problemType === "multi-choice") && (
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
              Detected Choices
            </div>
            {(() => {
              const choices = parseOptions(formData.text);
              return choices.length > 0 ? (
                <div data-testid="detected-choices" style={{ fontSize: "14px", color: "var(--color-text)" }}>
                  <span data-testid="detected-choices-count">{choices.length} choice{choices.length !== 1 ? "s" : ""} detected</span>
                  <div style={{ marginTop: "4px", display: "flex", flexDirection: "column", gap: "2px" }}>
                    {choices.map((choice, index) => (
                      <div key={index}>{choice}</div>
                    ))}
                  </div>
                </div>
              ) : (
                <div data-testid="detected-choices" style={{ fontSize: "14px", color: "var(--color-text-muted)" }}>
                  No choices detected
                </div>
              );
            })()}
          </div>
        )}

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
