import { describe, it, expect } from "vitest";

import { getRequiredFieldGaps, statusLabel } from "./BulkReviewStep.helpers";

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
