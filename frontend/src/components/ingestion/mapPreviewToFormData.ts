import type { IngestionPreview, WizardFormData } from "../IngestionWizard";

export function mapPreviewToFormData(preview: IngestionPreview): WizardFormData {
  return {
    text: preview.draft.text || "",
    problemType: preview.draft.problemType || "",
    graphDsl: preview.draft.graphDsl || "",
    correctAnswer: preview.draft.correctAnswer || "",
    tags: preview.draft.tags,
    subject: preview.draft.subject || "math",
  };
}
