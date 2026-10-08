import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { VariantReviewPanel } from "./VariantReviewPanel";
import type { VariantReviewActionState } from "./VariantReviewPanel";
import type { BulkDraft, BulkItemVariation } from "@/types/bulkIngestion";

function makeActionState(
  overrides: Partial<VariantReviewActionState> = {},
): VariantReviewActionState {
  return {
    isActionWorking: false,
    hasSaveFailed: false,
    hasConflict: false,
    sourceLocked: false,
    generateDisabledReason: "",
    generateHint: "",
    generateError: "",
    revalidating: false,
    revalidateError: "",
    attesting: false,
    attestError: "",
    revalidateDisabledReason: "",
    attestDisabledReason: "",
    ...overrides,
  };
}

function makeDraft(overrides: Partial<BulkDraft> = {}): BulkDraft {
  return {
    text: "What is 2+2?",
    problemType: "short-answer",
    graphDsl: "",
    correctAnswer: "4",
    tags: ["math"],
    subject: "math",
    ...overrides,
  };
}

function makeVariation(
  overrides: Partial<BulkItemVariation> = {},
): BulkItemVariation {
  return {
    status: "ready",
    generationCount: 1,
    original: {
      text: "What is 2+2?",
      problemType: "short-answer",
      graphDsl: null,
      correctAnswer: "4",
      subject: "math",
    },
    candidate: {
      text: "What is 3+3?",
      problemType: "short-answer",
      graphDsl: null,
      correctAnswer: "6",
    },
    validation: null,
    validatedRevision: null,
    attestation: null,
    queuedAt: null,
    ...overrides,
  };
}

function renderPanel(
  props: Partial<Parameters<typeof VariantReviewPanel>[0]> = {},
) {
  const handlers = {
    onTargetChange: vi.fn(),
    onUpdateDraft: vi.fn(),
    onGenerate: vi.fn(),
    onRevalidate: vi.fn(),
    onAttest: vi.fn(),
    onTagsChange: vi.fn(),
  };
  render(
    <VariantReviewPanel
      status="ready"
      variation={makeVariation()}
      draft={makeDraft()}
      requiredFieldGaps={{ text: false, problemType: false, correctAnswer: false }}
      imageSource="http://example.com/crop.png"
      target="candidate"
      onTargetChange={handlers.onTargetChange}
      isFieldDisabled={false}
      showGenerate
      actionState={makeActionState()}
      onUpdateDraft={handlers.onUpdateDraft}
      onGenerate={handlers.onGenerate}
      onRevalidate={handlers.onRevalidate}
      onAttest={handlers.onAttest}
      reviewTagSuggestions={["algebra"]}
      recentTags={["geometry"]}
      onTagsChange={handlers.onTagsChange}
      {...props}
    />,
  );
  return handlers;
}

describe("VariantReviewPanel", () => {
  it("shows the stale-pass keep-validation action for needs-validation with a pass verdict", () => {
    renderPanel({
      variation: makeVariation({
        status: "needs-validation",
        validation: { verdict: "pass", failures: [], reports: [] },
      }),
    });

    expect(screen.getByTestId("bulk-review-attest")).toHaveTextContent(
      "Keep validation",
    );
    expect(screen.getByTestId("bulk-review-stale-validation")).toBeInTheDocument();
    expect(screen.getByTestId("bulk-review-revalidate")).toBeInTheDocument();
    expect(screen.queryByTestId("bulk-review-attest-fail")).not.toBeInTheDocument();
  });

  it("shows the fail-override action only when canAttestVariant inputs allow it", () => {
    const attestableFailures = [
      { kind: "check", evidence: "notation" },
      { kind: "answer", evidence: "rounding" },
    ];
    renderPanel({
      status: "failed",
      variation: makeVariation({
        status: "failed",
        validation: { verdict: "fail", failures: attestableFailures, reports: [] },
      }),
    });

    expect(screen.getByTestId("bulk-review-generate")).toHaveTextContent(
      "Generate Again",
    );
    expect(screen.getByTestId("bulk-review-attest-fail")).toBeInTheDocument();
    expect(screen.getByTestId("bulk-review-evidence")).toBeInTheDocument();
  });

  it("hides the fail-override action for a content-kind failure", () => {
    renderPanel({
      status: "failed",
      variation: makeVariation({
        status: "failed",
        validation: {
          verdict: "fail",
          failures: [{ kind: "content", evidence: "changed the question" }],
          reports: [],
        },
      }),
    });

    expect(screen.getByTestId("bulk-review-generate")).toHaveTextContent(
      "Generate Again",
    );
    expect(screen.queryByTestId("bulk-review-attest-fail")).not.toBeInTheDocument();
  });

  it("keeps candidate fields editable with sourceReadOnly on the candidate target", () => {
    renderPanel({ sourceReadOnly: true });
    expect(screen.getByTestId("bulk-review-text")).toBeEnabled();
    expect(screen.getByTestId("bulk-review-subject")).toBeDisabled();
  });

  it("disables the source fields with sourceReadOnly on the source target", () => {
    renderPanel({ sourceReadOnly: true, target: "source" });

    expect(screen.getByTestId("bulk-review-text")).toBeDisabled();
    expect(screen.getByTestId("bulk-review-type")).toBeDisabled();
    expect(screen.getByTestId("bulk-review-answer")).toBeDisabled();
    expect(screen.getByTestId("bulk-review-graphdsl")).toBeDisabled();
    expect(screen.getByTestId("bulk-review-subject")).toBeDisabled();
  });

  it("leaves source fields editable without sourceReadOnly", () => {
    renderPanel({ target: "source" });
    expect(screen.getByTestId("bulk-review-text")).toBeEnabled();
    expect(screen.getByTestId("bulk-review-subject")).toBeEnabled();
  });

  it("hides the preview image when no imageSource is provided", () => {
    renderPanel({ imageSource: "" });
    expect(screen.queryByTestId("bulk-review-preview")).not.toBeInTheDocument();
  });

  it("renders the preview image from the provided imageSource", () => {
    renderPanel({ imageSource: "http://example.com/other.png" });
    expect(screen.getByTestId("bulk-review-preview")).toHaveAttribute(
      "src",
      "http://example.com/other.png",
    );
  });

  it("renders the extra-actions slot only when provided", () => {
    renderPanel();
    expect(screen.queryByTestId("panel-extra-action")).not.toBeInTheDocument();

    renderPanel({
      extraActions: <button type="button" data-testid="panel-extra-action">Submit</button>,
    });
    expect(screen.getByTestId("panel-extra-action")).toBeInTheDocument();
  });
});
