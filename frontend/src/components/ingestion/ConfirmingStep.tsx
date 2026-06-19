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
    <div style={{ padding: "2rem" }}>
      <h3 style={{ margin: "0 0 1.5rem", fontSize: "1.25rem", fontWeight: 800 }}>
        Confirm Problem
      </h3>

      <div
        style={{
          backgroundColor: "var(--color-surface-muted)",
          borderRadius: "var(--radius-lg)",
          padding: "1.5rem",
          border: "1px solid var(--color-border)",
          marginBottom: "1.5rem",
          display: "flex",
          flexDirection: "column",
          gap: "1.25rem"
        }}
      >
        <div>
          <div
            style={{
              fontSize: "0.75rem",
              fontWeight: 700,
              color: "var(--color-text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              marginBottom: "0.375rem",
            }}
          >
            Problem Text
          </div>
          <LatexText
            text={formData.text || "(empty)"}
            style={{ fontSize: "0.95rem", color: "var(--color-text)", lineHeight: "1.5" }}
          />
        </div>

        <div>
          <div
            style={{
              fontSize: "0.75rem",
              fontWeight: 700,
              color: "var(--color-text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              marginBottom: "0.375rem",
            }}
          >
            Type
          </div>
          <span
            className="badge badge-muted"
            style={{
              padding: "0.2rem 0.6rem",
              borderRadius: "var(--radius-full)",
              fontSize: "0.75rem",
              fontWeight: 600,
              textTransform: "none",
              letterSpacing: "normal"
            }}
          >
            {formData.problemType || "(empty)"}
          </span>
        </div>

        <div>
          <div
            style={{
              fontSize: "0.75rem",
              fontWeight: 700,
              color: "var(--color-text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              marginBottom: "0.375rem",
            }}
          >
            Subject
          </div>
          <span
            className="badge badge-muted"
            style={{
              padding: "0.2rem 0.6rem",
              borderRadius: "var(--radius-full)",
              fontSize: "0.75rem",
              fontWeight: 600,
              textTransform: "none",
              letterSpacing: "normal"
            }}
          >
            {formData.subject || "(empty)"}
          </span>
        </div>

        <div>
          <div
            style={{
              fontSize: "0.75rem",
              fontWeight: 700,
              color: "var(--color-text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              marginBottom: "0.375rem",
            }}
          >
            Graph DSL
          </div>
          <div
            style={{
              fontSize: "0.8125rem",
              color: "var(--color-text)",
              fontFamily: "monospace",
              backgroundColor: "var(--color-bg)",
              padding: "0.75rem",
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border)",
              whiteSpace: "pre-wrap",
              overflowX: "auto"
            }}
          >
            {formData.graphDsl || "(empty)"}
          </div>
        </div>

        <div>
          <div
            style={{
              fontSize: "0.75rem",
              fontWeight: 700,
              color: "var(--color-text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              marginBottom: "0.375rem",
            }}
          >
            Correct Answer
          </div>
          <div style={{ fontSize: "0.95rem", color: "var(--color-primary-text)", fontWeight: 600 }}>
            {formData.correctAnswer || "(empty)"}
          </div>
        </div>

        {(formData.problemType === "single-choice" || formData.problemType === "multi-choice") && (
          <div>
            <div
              style={{
                fontSize: "0.75rem",
                fontWeight: 700,
                color: "var(--color-text-muted)",
                textTransform: "uppercase",
                letterSpacing: "0.05em",
                marginBottom: "0.375rem",
              }}
            >
              Detected Choices
            </div>
            {(() => {
              const choices = parseOptions(formData.text);
              return choices.length > 0 ? (
                <div data-testid="detected-choices" style={{ fontSize: "0.9rem", color: "var(--color-text)", fontWeight: 500 }}>
                  <span data-testid="detected-choices-count" style={{ fontWeight: 600, color: "var(--color-text-muted)" }}>{choices.length} choice{choices.length !== 1 ? "s" : ""} detected</span>
                  <div style={{ marginTop: "0.375rem", display: "flex", flexDirection: "column", gap: "0.25rem" }}>
                    {choices.map((choice, index) => (
                      <div key={index}>{choice}</div>
                    ))}
                  </div>
                </div>
              ) : (
                <div data-testid="detected-choices" style={{ fontSize: "0.9rem", color: "var(--color-text-muted)" }}>
                  No choices detected
                </div>
              );
            })()}
          </div>
        )}

        <div>
          <div
            style={{
              fontSize: "0.75rem",
              fontWeight: 700,
              color: "var(--color-text-muted)",
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              marginBottom: "0.375rem",
            }}
          >
            Tags
          </div>
          <div style={{ display: "flex", gap: "0.375rem", flexWrap: "wrap", marginTop: "0.25rem" }}>
            {formData.tags.length > 0 ? (
              formData.tags.map((tag) => (
                <span
                  key={tag}
                  className="badge badge-muted"
                  style={{
                    padding: "0.2rem 0.6rem",
                    borderRadius: "var(--radius-full)",
                    fontSize: "0.75rem",
                    fontWeight: 600,
                    textTransform: "none",
                    letterSpacing: "normal"
                  }}
                >
                  {tag}
                </span>
              ))
            ) : (
              <span style={{ fontSize: "0.9rem", color: "var(--color-text-muted)" }}>(empty)</span>
            )}
          </div>
        </div>
      </div>

      {formData.graphDsl && (
        <div style={{ marginBottom: "1.5rem" }}>
          <div
            style={{
              fontSize: "0.75rem",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              color: "var(--color-text-muted)",
              marginBottom: "0.5rem",
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
            <div 
              className="badge badge-danger"
              style={{ 
                marginTop: "0.75rem",
                padding: "0.5rem 0.75rem",
                fontSize: "0.875rem",
                textTransform: "none",
                letterSpacing: "normal",
                fontWeight: 500,
                display: "block"
              }}
            >
              {graphError}
            </div>
          )}
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
            display: "block",
            marginBottom: "1.5rem"
          }}
        >
          {error}
        </div>
      )}

      <div style={{ display: "flex", gap: "0.75rem", borderTop: "1px solid var(--color-border)", paddingTop: "1.5rem", marginTop: "1rem" }}>
        <button
          onClick={onConfirm}
          disabled={isLoading}
          className="btn btn-primary"
          style={{
            padding: "0.5rem 1.25rem",
            fontSize: "0.875rem",
            fontWeight: 700,
          }}
          data-testid="confirm-button"
        >
          {isLoading ? "Creating..." : "Confirm & Save"}
        </button>
        <button
          onClick={() => setCurrentStep("editing")}
          disabled={isLoading}
          className="btn btn-secondary"
          style={{
            padding: "0.5rem 1.25rem",
            fontSize: "0.875rem",
            fontWeight: 700,
          }}
        >
          Back to Edit
        </button>
      </div>
    </div>
  );
}
