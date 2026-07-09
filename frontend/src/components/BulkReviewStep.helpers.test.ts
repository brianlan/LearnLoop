import { describe, it, expect } from "vitest";

import type { BulkDraft, BulkItem } from "@/types/bulkIngestion";
import {
  defaultDraft,
  getRequiredFieldGaps,
  retryDelayMs,
  serializeDraft,
  statusLabel,
} from "./BulkReviewStep.helpers";

function makeItem(draft: Partial<BulkDraft> = {}): BulkItem {
  return {
    itemId: "item-1",
    imageId: "img-1",
    batchId: "batch-1",
    status: "ready",
    order: 0,
    draft,
    extraction: {},
    retryCount: 0,
    submit: {},
    origin: {},
    createdAt: "",
    updatedAt: "",
  };
}

describe("retryDelayMs", () => {
  it("preserves the base retry value at zero failures", () => {
    expect(retryDelayMs(0)).toBe(500);
  });

  it("grows exponentially with the failure count", () => {
    expect(retryDelayMs(1)).toBe(1000);
    expect(retryDelayMs(2)).toBe(2000);
  });

  it("caps at the maximum retry value", () => {
    expect(retryDelayMs(3)).toBe(4000);
    expect(retryDelayMs(4)).toBe(4000);
    expect(retryDelayMs(10)).toBe(4000);
  });
});

describe("defaultDraft", () => {
  it("fills defaults for an empty draft", () => {
    expect(defaultDraft(makeItem({}))).toEqual({
      text: "",
      problemType: "short-answer",
      graphDsl: "",
      correctAnswer: "",
      tags: [],
      subject: "math",
    });
  });

  it("fills defaults for null fields", () => {
    expect(
      defaultDraft(
        makeItem({
          text: null,
          problemType: null,
          graphDsl: null,
          correctAnswer: null,
          tags: undefined,
          subject: null,
        }),
      ),
    ).toEqual({
      text: "",
      problemType: "short-answer",
      graphDsl: "",
      correctAnswer: "",
      tags: [],
      subject: "math",
    });
  });

  it("preserves provided draft values", () => {
    const draft: BulkDraft = {
      text: "What is 2+2?",
      problemType: "single-choice",
      graphDsl: "graph-dsl",
      correctAnswer: "4",
      tags: ["math", "arithmetic"],
      subject: "english",
    };
    expect(defaultDraft(makeItem(draft))).toEqual(draft);
  });
});

describe("serializeDraft", () => {
  it("returns a JSON string equal to JSON.stringify", () => {
    const draft: BulkDraft = {
      text: "x",
      problemType: "short-answer",
      graphDsl: "",
      correctAnswer: "y",
      tags: ["t"],
      subject: "math",
    };
    expect(serializeDraft(draft)).toBe(JSON.stringify(draft));
  });

  it("produces equal output for equal drafts", () => {
    const draft: BulkDraft = { text: "a", correctAnswer: "b" };
    expect(serializeDraft(draft)).toBe(serializeDraft({ ...draft }));
  });
});

describe("statusLabel", () => {
  it.each([
    ["queued", "Queued"],
    ["extracting", "Extracting..."],
    ["ready", "Ready"],
    ["failed", "Extraction failed"],
    ["submit-failed", "Submit failed"],
    ["deleted", "Deleted"],
    ["submitted", "Submitted"],
  ])("maps status %s to its label", (status, label) => {
    expect(statusLabel(status)).toBe(label);
  });

  it("returns the raw status for unknown values", () => {
    expect(statusLabel("unexpected")).toBe("unexpected");
    expect(statusLabel("")).toBe("");
  });
});

describe("getRequiredFieldGaps", () => {
  it("reports no gaps for a complete draft", () => {
    expect(
      getRequiredFieldGaps({
        text: "Question",
        problemType: "short-answer",
        correctAnswer: "Answer",
      }),
    ).toEqual({ text: false, problemType: false, correctAnswer: false });
  });

  it("reports a gap when text is empty or whitespace", () => {
    expect(getRequiredFieldGaps({ text: "", problemType: "x", correctAnswer: "y" }).text).toBe(true);
    expect(getRequiredFieldGaps({ text: "   ", problemType: "x", correctAnswer: "y" }).text).toBe(true);
  });

  it("reports a gap when text is missing", () => {
    expect(getRequiredFieldGaps({ problemType: "x", correctAnswer: "y" }).text).toBe(true);
  });

  it("reports a gap when problemType is missing", () => {
    expect(getRequiredFieldGaps({ text: "x", correctAnswer: "y" }).problemType).toBe(true);
  });

  it("reports a gap when correctAnswer is empty or whitespace", () => {
    expect(getRequiredFieldGaps({ text: "x", problemType: "y", correctAnswer: "" }).correctAnswer).toBe(true);
    expect(getRequiredFieldGaps({ text: "x", problemType: "y", correctAnswer: "  " }).correctAnswer).toBe(true);
  });

  it("reports all gaps for an empty draft", () => {
    expect(getRequiredFieldGaps({})).toEqual({ text: true, problemType: true, correctAnswer: true });
  });
});
