import { GraphSandbox } from "../GraphSandbox";
import { TagInput } from "../TagInput";
import { parseOptions } from "../AnswerInput";
import type { IngestionPreview, WizardFormData, WizardStep } from "../IngestionWizard";
import { PROBLEM_TYPE_OPTIONS } from "@/constants/problemTypes";

export interface EditingStepProps {
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

export function EditingStep({
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
    <div style={{ padding: "2rem" }}>
      <h3 style={{ margin: "0 0 1.5rem", fontSize: "1.25rem", fontWeight: 800 }}>
        Edit Problem Details
      </h3>

      {previewId && preview?.sourceImage && (
        <div style={{ marginBottom: "1.5rem" }}>
          <label
            style={{
              fontSize: "0.75rem",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              color: "var(--color-text-muted)",
              display: "block",
              marginBottom: "0.5rem"
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
              borderRadius: "var(--radius-md)",
              border: "1px solid var(--color-border)",
              boxShadow: "0 4px 12px rgba(0,0,0,0.08)",
            }}
            data-testid="source-image"
          />
        </div>
      )}

      <div style={{ marginBottom: "1.5rem" }}>
        <label
          style={{
            fontSize: "0.75rem",
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
            color: "var(--color-text-muted)",
            display: "block",
            marginBottom: "0.5rem"
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
            padding: "0.6rem 0.8rem",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-md)",
            fontSize: "0.95rem",
            fontFamily: "inherit",
            resize: "vertical",
            boxSizing: "border-box",
            backgroundColor: "var(--color-bg)",
            color: "var(--color-text)",
            outline: "none",
          }}
          placeholder="Enter the problem statement..."
          data-testid="text-input"
        />
      </div>

      <div style={{ marginBottom: "1.5rem" }}>
        <label
          style={{
            fontSize: "0.75rem",
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
            color: "var(--color-text-muted)",
            display: "block",
            marginBottom: "0.5rem"
          }}
        >
          Subject
        </label>
        <select
          value={formData.subject}
          onChange={(e) => onFieldChange("subject", e.target.value)}
          style={{
            width: "100%",
            padding: "0.6rem 0.8rem",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-md)",
            fontSize: "0.9rem",
            fontFamily: "inherit",
            boxSizing: "border-box",
            backgroundColor: "var(--color-bg)",
            color: "var(--color-text)",
            outline: "none",
          }}
          data-testid="subject-input"
        >
          <option value="math">Math</option>
          <option value="english">English</option>
        </select>
      </div>

      <div style={{ marginBottom: "1.5rem" }}>
        <label
          style={{
            fontSize: "0.75rem",
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
            color: "var(--color-text-muted)",
            display: "block",
            marginBottom: "0.5rem"
          }}
        >
          Problem Type
        </label>
        <select
          value={formData.problemType}
          onChange={(e) => onFieldChange("problemType", e.target.value)}
          style={{
            width: "100%",
            padding: "0.6rem 0.8rem",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-md)",
            fontSize: "0.9rem",
            fontFamily: "inherit",
            boxSizing: "border-box",
            backgroundColor: "var(--color-bg)",
            color: "var(--color-text)",
            outline: "none",
          }}
          data-testid="problem-type-input"
        >
          <option value="">Select a problem type…</option>
          {PROBLEM_TYPE_OPTIONS.map((option) => (
            <option key={option.value} value={option.value}>{option.label}</option>
          ))}
        </select>
      </div>

      <div style={{ marginBottom: "1.5rem" }}>
        <label
          style={{
            fontSize: "0.75rem",
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
            color: "var(--color-text-muted)",
            display: "block",
            marginBottom: "0.5rem"
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
            padding: "0.6rem 0.8rem",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-md)",
            fontSize: "0.875rem",
            fontFamily: "monospace",
            resize: "vertical",
            boxSizing: "border-box",
            backgroundColor: "var(--color-bg)",
            color: "var(--color-text)",
            outline: "none",
          }}
          placeholder="Enter JSXGraph DSL code..."
          data-testid="graph-dsl-input"
        />

        {formData.graphDsl && (
          <div style={{ marginTop: "1rem" }}>
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
              onRender={onClearGraphError}
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
      </div>

      <div style={{ marginBottom: "1.5rem" }}>
        <label
          style={{
            fontSize: "0.75rem",
            fontWeight: 700,
            textTransform: "uppercase",
            letterSpacing: "0.05em",
            color: "var(--color-text-muted)",
            display: "block",
            marginBottom: "0.5rem"
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
            padding: "0.6rem 0.8rem",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-md)",
            fontSize: "0.9rem",
            fontFamily: "inherit",
            boxSizing: "border-box",
            backgroundColor: "var(--color-bg)",
            color: "var(--color-text)",
            outline: "none",
          }}
          placeholder="Enter the correct answer..."
          data-testid="correct-answer-input"
        />
      </div>

      {(formData.problemType === "single-choice" || formData.problemType === "multi-choice") && (
        <div style={{ marginBottom: "1.5rem" }}>
          <label
            style={{
              fontSize: "0.75rem",
              fontWeight: 700,
              textTransform: "uppercase",
              letterSpacing: "0.05em",
              color: "var(--color-text-muted)",
              display: "block",
              marginBottom: "0.5rem"
            }}
          >
            Detected Choices
          </label>
          {(() => {
            const choices = parseOptions(formData.text);
            return choices.length > 0 ? (
              <div 
                data-testid="detected-choices" 
                style={{ 
                  padding: "0.75rem 1rem", 
                  backgroundColor: "var(--color-surface-muted)", 
                  borderRadius: "var(--radius-md)", 
                  border: "1px solid var(--color-border)" 
                }}
              >
                <div data-testid="detected-choices-count" style={{ fontSize: "0.8125rem", color: "var(--color-text-muted)", marginBottom: "0.5rem", fontWeight: 600 }}>
                  {choices.length} choice{choices.length !== 1 ? "s" : ""} detected
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "0.375rem" }}>
                  {choices.map((choice, index) => (
                    <div key={index} style={{ fontSize: "0.9rem", color: "var(--color-text)", fontWeight: 500 }}>
                      {choice}
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div data-testid="detected-choices" style={{ padding: "0.75rem 1rem", backgroundColor: "var(--color-surface-muted)", borderRadius: "var(--radius-md)", border: "1px solid var(--color-border)", fontSize: "0.9rem", color: "var(--color-text-muted)", fontWeight: 500 }}>
                No choices detected in problem text
              </div>
            );
          })()}
        </div>
      )}

      <div style={{ marginBottom: "1.5rem" }}>
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
          onClick={() => setCurrentStep("confirming")}
          className="btn btn-primary"
          style={{
            padding: "0.5rem 1.25rem",
            fontSize: "0.875rem",
            fontWeight: 700,
          }}
          data-testid="review-button"
        >
          Review & Confirm
        </button>
        <button
          onClick={onCancel}
          className="btn btn-secondary"
          style={{
            padding: "0.5rem 1.25rem",
            fontSize: "0.875rem",
            fontWeight: 700,
          }}
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
