import { GraphSandbox } from "../GraphSandbox";
import { TagInput } from "../TagInput";
import { parseOptions } from "../AnswerInput";
import type { IngestionPreview, WizardFormData, WizardStep } from "../IngestionWizard";

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
          Subject
        </label>
        <select
          value={formData.subject}
          onChange={(e) => onFieldChange("subject", e.target.value)}
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
          data-testid="subject-input"
        >
          <option value="math">Math</option>
          <option value="english">English</option>
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

      {(formData.problemType === "single-choice" || formData.problemType === "multi-choice") && (
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
            Detected Choices
          </label>
          {(() => {
            const choices = parseOptions(formData.text);
            return choices.length > 0 ? (
              <div data-testid="detected-choices" style={{ padding: "10px 12px", backgroundColor: "var(--color-surface-muted)", borderRadius: "6px", border: "1px solid var(--color-border)" }}>
                <div data-testid="detected-choices-count" style={{ fontSize: "12px", color: "var(--color-text-muted)", marginBottom: "8px" }}>
                  {choices.length} choice{choices.length !== 1 ? "s" : ""} detected
                </div>
                <div style={{ display: "flex", flexDirection: "column", gap: "4px" }}>
                  {choices.map((choice, index) => (
                    <div key={index} style={{ fontSize: "14px", color: "var(--color-text)", padding: "2px 0" }}>
                      {choice}
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <div data-testid="detected-choices" style={{ padding: "10px 12px", backgroundColor: "var(--color-surface-muted)", borderRadius: "6px", border: "1px solid var(--color-border)", fontSize: "14px", color: "var(--color-text-muted)" }}>
                No choices detected in problem text
              </div>
            );
          })()}
        </div>
      )}

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
