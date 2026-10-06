import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, waitFor, fireEvent, act } from "@testing-library/react";
import { BulkIngestionWizard } from "./BulkIngestionWizard";
import type { BatchResponse, BulkBatch, BulkImage, BulkItem } from "@/types/bulkIngestion";

const mocks = vi.hoisted(() => ({
  getActiveBatch: vi.fn<() => Promise<BatchResponse>>(),
  getBatch: vi.fn<() => Promise<BatchResponse>>(),
  createBatch: vi.fn<() => Promise<BatchResponse>>(),
  uploadBatchImages: vi.fn<() => Promise<BatchResponse>>(),
  detectImageBoxes: vi.fn<() => Promise<BatchResponse>>(),
  saveImageBoxes: vi.fn<() => Promise<BatchResponse>>(),
  commitImage: vi.fn<() => Promise<BatchResponse>>(),
  deleteImage: vi.fn<() => Promise<BatchResponse>>(),
  startBatchExtraction: vi.fn<() => Promise<BatchResponse>>(),
  submitBatch: vi.fn<() => Promise<{ submitSummary: { batchId: string; status: string; items: unknown[] } }>>(),
  retryItem: vi.fn<() => Promise<BatchResponse>>(),
  deleteBatchItem: vi.fn<() => Promise<BatchResponse>>(),
  undoDeleteBatchItem: vi.fn<() => Promise<BatchResponse>>(),
  updateItemDraft: vi.fn<() => Promise<BatchResponse>>(),
  editVariationCandidate: vi.fn<() => Promise<BatchResponse>>(),
  generateVariation: vi.fn<() => Promise<BatchResponse>>(),
  revalidateVariation: vi.fn<() => Promise<BatchResponse>>(),
}));

vi.mock("@/api/bulkIngestion", () => ({
  getActiveBatch: mocks.getActiveBatch,
  getBatch: mocks.getBatch,
  createBatch: mocks.createBatch,
  uploadBatchImages: mocks.uploadBatchImages,
  detectImageBoxes: mocks.detectImageBoxes,
  saveImageBoxes: mocks.saveImageBoxes,
  commitImage: mocks.commitImage,
  deleteImage: mocks.deleteImage,
  startBatchExtraction: mocks.startBatchExtraction,
  submitBatch: mocks.submitBatch,
  retryItem: mocks.retryItem,
  deleteBatchItem: mocks.deleteBatchItem,
  undoDeleteBatchItem: mocks.undoDeleteBatchItem,
  updateItemDraft: mocks.updateItemDraft,
  editVariationCandidate: mocks.editVariationCandidate,
  generateVariation: mocks.generateVariation,
  revalidateVariation: mocks.revalidateVariation,
}));

function makeImage(overrides: Partial<BulkImage> = {}): BulkImage {
  return {
    imageId: "img-1",
    status: "committed",
    order: 0,
    sourceImage: { bucket: "b", objectKey: "k" },
    boxes: [],
    detection: {},
    createdAt: "2026-07-03T00:00:00Z",
    updatedAt: "2026-07-03T00:00:00Z",
    ...overrides,
  };
}

function makeItem(overrides: Partial<BulkItem> = {}): BulkItem {
  return {
    itemId: "item-1",
    imageId: "img-1",
    batchId: "batch-1",
    status: "ready",
    order: 0,
    draft: {
      text: "What is 2+2?",
      problemType: "short-answer",
      graphDsl: "",
      correctAnswer: "4",
      tags: ["math"],
      subject: "math",
    },
    extraction: {},
    retryCount: 0,
    submit: {},
    origin: {},
    crop: { mediaUrl: "http://example.com/crop-item-1.png" },
    contentRevision: 0,
    variation: null,
    createdAt: "2026-07-03T00:00:00Z",
    updatedAt: "2026-07-03T00:00:00Z",
    ...overrides,
  };
}

function makeBatch(overrides: Partial<BulkBatch> = {}): BulkBatch {
  return {
    id: "batch-1",
    userId: "user-1",
    status: "active",
    ingestionMode: "original",
    images: [makeImage()],
    items: [makeItem()],
    createdAt: "2026-07-03T00:00:00Z",
    updatedAt: "2026-07-03T00:00:00Z",
    expiresAt: "2026-07-04T00:00:00Z",
    ...overrides,
  };
}

function readyVariantItem(overrides: Partial<BulkItem> = {}): BulkItem {
  return makeItem({
    contentRevision: 10,
    variation: {
      status: "ready",
      generationCount: 1,
      original: {
        text: "What is 2+2?",
        problemType: "short-answer",
        graphDsl: "",
        correctAnswer: "4",
        subject: "math",
      },
      candidate: {
        text: "What is 3+3?",
        problemType: "short-answer",
        graphDsl: "",
        correctAnswer: "6",
      },
      validation: { verdict: "pass" },
      validatedRevision: 10,
      attestation: null,
      queuedAt: null,
    },
    ...overrides,
  });
}

const reviewBatchResponse: BatchResponse = { batch: makeBatch() };

async function renderAtReviewStep(response: BatchResponse = reviewBatchResponse) {
  mocks.getActiveBatch.mockRejectedValue(
    new Error("No active batch found"),
  );
  mocks.getBatch.mockResolvedValue(response);

  render(<BulkIngestionWizard initialBatchId="batch-1" />);

  await waitFor(() => {
    expect(screen.getByTestId("bulk-wizard-review-step")).toBeInTheDocument();
  });
}

function getContinueButton() {
  return screen.getByTestId("bulk-review-continue") as HTMLButtonElement;
}

describe("BulkIngestionWizard integrated autosave characterization", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("gates Continue while a draft save is pending and re-enables it after success", async () => {
    let resolveSave: (value: BatchResponse) => void = () => undefined;
    mocks.updateItemDraft.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSave = resolve;
        }),
    );

    await renderAtReviewStep();
    const continueButton = getContinueButton();
    expect(continueButton).not.toBeDisabled();

    const answerInput = screen.getByTestId("bulk-review-answer");
    fireEvent.change(answerInput, { target: { value: "42" } });

    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    expect(mocks.updateItemDraft).toHaveBeenCalledTimes(1);
    expect(mocks.updateItemDraft).toHaveBeenCalledWith(
      "batch-1",
      "item-1",
      expect.objectContaining({ correctAnswer: "42" }),
      0,
    );
    expect(continueButton).toBeDisabled();
    expect(answerInput).not.toBeDisabled();
    expect(screen.queryByTestId("bulk-wizard-error")).not.toBeInTheDocument();

    await act(async () => {
      resolveSave({ batch: makeBatch({ items: [makeItem({
        draft: { ...makeItem().draft, correctAnswer: "42" },
        updatedAt: "2026-07-03T00:00:01Z",
      })] }) });
    });

    await waitFor(() => {
      expect(continueButton).not.toBeDisabled();
    });
  });

  it("keeps the review step mounted with a visible per-item error when a draft save rejects", async () => {
    mocks.updateItemDraft.mockRejectedValue(new Error("network error"));

    await renderAtReviewStep();
    const continueButton = getContinueButton();
    expect(continueButton).not.toBeDisabled();

    const answerInput = screen.getByTestId("bulk-review-answer");
    fireEvent.change(answerInput, { target: { value: "99" } });

    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    expect(mocks.updateItemDraft).toHaveBeenCalledTimes(1);

    // The rejection is reported per item while the editor stays mounted with
    // the dirty input intact; the wizard does not replace the review UI.
    await waitFor(() => {
      expect(screen.getByTestId("bulk-review-save-status")).toHaveTextContent(
        "Save failed, retrying...",
      );
    });
    expect(screen.getByTestId("bulk-wizard-review-step")).toBeInTheDocument();
    expect(screen.getByTestId("bulk-review-answer")).toHaveValue("99");
    expect(screen.queryByTestId("bulk-wizard-error")).not.toBeInTheDocument();
    expect(continueButton).toBeDisabled();

    // The same target is retried after the backoff delay without losing input.
    await act(async () => {
      vi.advanceTimersByTime(1500);
    });

    expect(mocks.updateItemDraft).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("bulk-review-answer")).toHaveValue("99");
    expect(screen.getByTestId("bulk-wizard-review-step")).toBeInTheDocument();
  });

  it("clears the per-item save failure after a successful retry and stops retrying", async () => {
    mocks.updateItemDraft
      .mockRejectedValueOnce(new Error("network error"))
      .mockResolvedValueOnce({
        batch: makeBatch({
          items: [makeItem({
            draft: { ...makeItem().draft, correctAnswer: "7" },
            updatedAt: "2026-07-03T00:00:01Z",
          })],
        }),
      });

    await renderAtReviewStep();
    const continueButton = getContinueButton();

    const answerInput = screen.getByTestId("bulk-review-answer");
    fireEvent.change(answerInput, { target: { value: "7" } });

    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    await waitFor(() => {
      expect(screen.getByTestId("bulk-review-save-status")).toBeInTheDocument();
    });

    await act(async () => {
      vi.advanceTimersByTime(1000);
    });

    await waitFor(() => {
      expect(
        screen.queryByTestId("bulk-review-save-status"),
      ).not.toBeInTheDocument();
    });

    expect(screen.getByTestId("bulk-wizard-review-step")).toBeInTheDocument();
    expect(screen.getByTestId("bulk-review-answer")).toHaveValue("7");
    await waitFor(() => {
      expect(continueButton).not.toBeDisabled();
    });

    await act(async () => {
      vi.advanceTimersByTime(10000);
    });
    expect(mocks.updateItemDraft).toHaveBeenCalledTimes(2);
  });

  it("routes candidate edits to the candidate endpoint and source edits to the draft endpoint", async () => {
    const itemWithCandidate = makeItem({
      contentRevision: 3,
      variation: {
        status: "ready",
        generationCount: 1,
        original: {
          text: "What is 2+2?",
          problemType: "short-answer",
          graphDsl: "",
          correctAnswer: "4",
          subject: "math",
        },
        candidate: {
          text: "What is 3+3?",
          problemType: "short-answer",
          graphDsl: "",
          correctAnswer: "6",
        },
        validation: { verdict: "pass" },
        validatedRevision: 3,
        attestation: null,
        queuedAt: null,
      },
    });
    const batchWithCandidate = makeBatch({ items: [itemWithCandidate] });
    mocks.editVariationCandidate.mockResolvedValue({
      batch: batchWithCandidate,
    });
    mocks.updateItemDraft.mockResolvedValue({ batch: batchWithCandidate });

    await renderAtReviewStep({ batch: batchWithCandidate });

    fireEvent.change(screen.getByTestId("bulk-review-answer"), {
      target: { value: "66" },
    });
    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    await waitFor(() => {
      expect(mocks.editVariationCandidate).toHaveBeenCalledWith(
        "batch-1",
        "item-1",
        expect.objectContaining({
          expectedRevision: 3,
          correctAnswer: "66",
          // Empty graphDsl is sent as null: "" vs the stored None would
          // count as a semantic change and falsely invalidate the
          // candidate on tags-only saves.
          graphDsl: null,
          // Shared draft tags are preserved on candidate saves.
          tags: ["math"],
        }),
      );
    });
    expect(mocks.updateItemDraft).not.toHaveBeenCalled();

    fireEvent.click(screen.getByTestId("bulk-review-edit-source"));
    fireEvent.change(screen.getByTestId("bulk-review-answer"), {
      target: { value: "44" },
    });
    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    await waitFor(() => {
      expect(mocks.updateItemDraft).toHaveBeenCalledWith(
        "batch-1",
        "item-1",
        expect.objectContaining({ correctAnswer: "44" }),
        3,
      );
    });
  });

  it("keeps a withdrawn candidate hidden after an older save response arrives", async () => {
    let resolveOldSave: (value: BatchResponse) => void = () => undefined;
    mocks.editVariationCandidate.mockImplementation(
      () => new Promise((resolve) => { resolveOldSave = resolve; }),
    );
    const g1 = readyVariantItem();
    const initial = makeBatch({ ingestionMode: "transfer-variant", revision: 20, items: [g1] });
    const queuedItem = readyVariantItem({
      contentRevision: 11,
      updatedAt: "2026-07-03T00:00:02Z",
      variation: {
        ...g1.variation!,
        status: "queued",
        generationCount: 2,
        candidate: null,
        validation: null,
        validatedRevision: null,
      },
    });
    const queued = makeBatch({
      ingestionMode: "transfer-variant",
      revision: 22,
      items: [queuedItem],
      updatedAt: "2026-07-03T00:00:02Z",
    });
    mocks.generateVariation.mockResolvedValue({ batch: queued });
    await renderAtReviewStep({ batch: initial });

    // A no-op candidate save keeps the item content revision at r10 while
    // its older batch response is delayed past the G2 queued response.
    fireEvent.change(screen.getByTestId("bulk-review-answer"), { target: { value: "66" } });
    fireEvent.change(screen.getByTestId("bulk-review-answer"), { target: { value: "6" } });
    await act(async () => { vi.advanceTimersByTime(600); });
    expect(mocks.editVariationCandidate).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId("bulk-review-generate"));
    await waitFor(() => {
      expect(screen.queryByTestId("bulk-review-edit-candidate")).not.toBeInTheDocument();
    });
    await act(async () => {
      resolveOldSave({ batch: makeBatch({
        ingestionMode: "transfer-variant",
        revision: 21,
        items: [readyVariantItem({
          updatedAt: "2026-07-03T00:00:01Z",
        })],
        updatedAt: "2026-07-03T00:00:01Z",
      }) });
    });

    fireEvent.change(screen.getByTestId("bulk-review-answer"), {
      target: { value: "Edited after old response" },
    });
    await act(async () => { vi.advanceTimersByTime(600); });
    expect(screen.queryByTestId("bulk-review-edit-candidate")).not.toBeInTheDocument();
    expect(mocks.editVariationCandidate).toHaveBeenCalledTimes(1);
  });

  it("lets source tags save after this page's candidate save advances the item revision", async () => {
    let resolveCandidate: (value: BatchResponse) => void = () => undefined;
    const initialItem = readyVariantItem();
    const initial = makeBatch({
      ingestionMode: "transfer-variant",
      revision: 20,
      items: [initialItem],
    });
    const candidateSaved = readyVariantItem({
      contentRevision: 11,
      updatedAt: "2026-07-03T00:00:01Z",
      variation: {
        ...initialItem.variation!,
        status: "needs-validation",
        candidate: { ...initialItem.variation!.candidate!, correctAnswer: "66" },
        validation: null,
        validatedRevision: null,
      },
    });
    const candidateResponse = makeBatch({
      ingestionMode: "transfer-variant",
      revision: 21,
      items: [candidateSaved],
      updatedAt: "2026-07-03T00:00:01Z",
    });
    const taggedCandidate = {
      ...candidateSaved,
      draft: { ...candidateSaved.draft, tags: ["math", "calculus"] },
      updatedAt: "2026-07-03T00:00:02Z",
    };
    mocks.editVariationCandidate
      .mockImplementationOnce(() => new Promise((resolve) => { resolveCandidate = resolve; }))
      .mockResolvedValue({ batch: makeBatch({
        ingestionMode: "transfer-variant", revision: 22, items: [taggedCandidate],
      }) });
    mocks.updateItemDraft.mockResolvedValue({ batch: makeBatch({
      ingestionMode: "transfer-variant",
      revision: 23,
      items: [{ ...taggedCandidate, updatedAt: "2026-07-03T00:00:03Z" }],
      updatedAt: "2026-07-03T00:00:03Z",
    }) });
    await renderAtReviewStep({ batch: initial });

    fireEvent.change(screen.getByTestId("bulk-review-answer"), { target: { value: "66" } });
    await act(async () => { vi.advanceTimersByTime(600); });
    expect(mocks.editVariationCandidate).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByTestId("bulk-review-edit-source"));
    fireEvent.change(screen.getByTestId("bulk-review-tags-field"), {
      target: { value: "calculus" },
    });
    fireEvent.keyDown(screen.getByTestId("bulk-review-tags-field"), { key: "Enter", code: "Enter" });

    await act(async () => { resolveCandidate({ batch: candidateResponse }); });
    await act(async () => { vi.advanceTimersByTime(600); });
    await act(async () => { vi.advanceTimersByTime(600); });
    expect(mocks.updateItemDraft).toHaveBeenCalledWith(
      "batch-1",
      "item-1",
      expect.objectContaining({ tags: ["math", "calculus"] }),
      11,
    );
    expect(screen.queryByTestId("bulk-review-save-status")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("bulk-review-edit-candidate"));
    expect(screen.getByTestId("bulk-review-answer")).toHaveValue("66");
    expect(screen.getByTestId("bulk-review-tags-tag-calculus")).toBeInTheDocument();
  });

  it("keeps source tags when a later candidate edit saves after the source response", async () => {
    let resolveSource: (value: BatchResponse) => void = () => undefined;
    mocks.updateItemDraft.mockImplementation(
      () => new Promise((resolve) => { resolveSource = resolve; }),
    );
    const initialItem = readyVariantItem();
    const initial = makeBatch({
      ingestionMode: "transfer-variant",
      revision: 20,
      items: [initialItem],
    });
    const sourceSaved = readyVariantItem({
      draft: { ...initialItem.draft, tags: ["math", "calculus"] },
      updatedAt: "2026-07-03T00:00:01Z",
    });
    mocks.editVariationCandidate.mockResolvedValue({ batch: makeBatch({
      ingestionMode: "transfer-variant",
      revision: 22,
      items: [readyVariantItem({
        ...sourceSaved,
        contentRevision: 11,
        updatedAt: "2026-07-03T00:00:02Z",
        variation: {
          ...initialItem.variation!,
          status: "needs-validation",
          candidate: { ...initialItem.variation!.candidate!, correctAnswer: "66" },
          validation: null,
          validatedRevision: null,
        },
      })],
    }) });
    await renderAtReviewStep({ batch: initial });

    fireEvent.click(screen.getByTestId("bulk-review-edit-source"));
    fireEvent.change(screen.getByTestId("bulk-review-tags-field"), {
      target: { value: "calculus" },
    });
    fireEvent.keyDown(screen.getByTestId("bulk-review-tags-field"), { key: "Enter", code: "Enter" });
    await act(async () => { vi.advanceTimersByTime(600); });
    expect(mocks.updateItemDraft).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId("bulk-review-edit-candidate"));
    fireEvent.change(screen.getByTestId("bulk-review-answer"), { target: { value: "66" } });
    await act(async () => { resolveSource({ batch: makeBatch({
      ingestionMode: "transfer-variant",
      revision: 21,
      items: [sourceSaved],
      updatedAt: "2026-07-03T00:00:01Z",
    }) }); });
    await act(async () => { vi.advanceTimersByTime(600); });

    expect(mocks.editVariationCandidate).toHaveBeenCalledWith(
      "batch-1",
      "item-1",
      expect.objectContaining({
        expectedRevision: 10,
        correctAnswer: "66",
        tags: ["math", "calculus"],
      }),
    );
    expect(screen.queryByTestId("bulk-review-save-status")).not.toBeInTheDocument();
  });

  it.each([
    { change: "add", initialTags: ["math"], sourceTags: ["math", "algebra"], finalTags: ["math", "algebra", "geometry"] },
    { change: "remove", initialTags: ["math", "obsolete"], sourceTags: ["math"], finalTags: ["math", "geometry"] },
  ])("shares tag $change edits across source and candidate saves", async ({ change, initialTags, sourceTags, finalTags }) => {
    let resolveFirstSource: (value: BatchResponse) => void = () => undefined;
    const initialItem = readyVariantItem({
      draft: { ...makeItem().draft, tags: initialTags },
    });
    const sourceSaved = readyVariantItem({
      draft: { ...initialItem.draft, tags: sourceTags },
      updatedAt: "2026-07-03T00:00:01Z",
    });
    const finalItem = readyVariantItem({
      draft: { ...initialItem.draft, tags: finalTags },
      updatedAt: "2026-07-03T00:00:02Z",
    });
    mocks.updateItemDraft
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirstSource = resolve; }))
      .mockResolvedValue({ batch: makeBatch({
        ingestionMode: "transfer-variant", revision: 22, items: [finalItem],
      }) });
    mocks.editVariationCandidate.mockResolvedValue({ batch: makeBatch({
      ingestionMode: "transfer-variant", revision: 23, items: [finalItem],
    }) });
    await renderAtReviewStep({ batch: makeBatch({
      ingestionMode: "transfer-variant", revision: 20, items: [initialItem],
    }) });

    fireEvent.click(screen.getByTestId("bulk-review-edit-source"));
    if (change === "add") {
      fireEvent.change(screen.getByTestId("bulk-review-tags-field"), { target: { value: "algebra" } });
      fireEvent.keyDown(screen.getByTestId("bulk-review-tags-field"), { key: "Enter", code: "Enter" });
    } else {
      fireEvent.click(screen.getByTestId("bulk-review-tags-remove-obsolete"));
    }
    await act(async () => { vi.advanceTimersByTime(600); });
    expect(mocks.updateItemDraft).toHaveBeenCalledTimes(1);

    fireEvent.click(screen.getByTestId("bulk-review-edit-candidate"));
    for (const tag of sourceTags) {
      expect(screen.getByTestId(`bulk-review-tags-tag-${tag}`)).toBeInTheDocument();
    }
    if (change === "remove") {
      expect(screen.queryByTestId("bulk-review-tags-tag-obsolete")).not.toBeInTheDocument();
    }
    fireEvent.change(screen.getByTestId("bulk-review-tags-field"), { target: { value: "geometry" } });
    fireEvent.keyDown(screen.getByTestId("bulk-review-tags-field"), { key: "Enter", code: "Enter" });

    await act(async () => { resolveFirstSource({ batch: makeBatch({
      ingestionMode: "transfer-variant", revision: 21, items: [sourceSaved],
    }) }); });
    await act(async () => { vi.advanceTimersByTime(1200); });
    await waitFor(() => { expect(mocks.editVariationCandidate).toHaveBeenCalled(); });
    expect(mocks.editVariationCandidate).toHaveBeenLastCalledWith(
      "batch-1", "item-1", expect.objectContaining({ tags: finalTags }),
    );
    fireEvent.click(screen.getByTestId("bulk-review-edit-source"));
    for (const tag of finalTags) {
      expect(screen.getByTestId(`bulk-review-tags-tag-${tag}`)).toBeInTheDocument();
    }
    fireEvent.click(screen.getByTestId("bulk-review-edit-candidate"));
    for (const tag of finalTags) {
      expect(screen.getByTestId(`bulk-review-tags-tag-${tag}`)).toBeInTheDocument();
    }
    expect(screen.queryByTestId("bulk-review-save-status")).not.toBeInTheDocument();
  });

  it.each([
    { change: "add", pendingTags: ["math", "algebra", "geometry"], finalTags: ["math", "algebra", "geometry", "trigonometry"] },
    { change: "undo", pendingTags: ["math"], finalTags: ["math", "trigonometry"] },
  ])("keeps source tag $change edits in both views after an older save returns", async ({ change, pendingTags, finalTags }) => {
    let resolveFirstSource: (value: BatchResponse) => void = () => undefined;
    const initialItem = readyVariantItem();
    const firstSaved = readyVariantItem({
      draft: { ...initialItem.draft, tags: ["math", "algebra"] },
      updatedAt: "2026-07-03T00:00:01Z",
    });
    const finalItem = readyVariantItem({
      draft: { ...initialItem.draft, tags: finalTags },
      updatedAt: "2026-07-03T00:00:02Z",
    });
    mocks.updateItemDraft
      .mockImplementationOnce(() => new Promise((resolve) => { resolveFirstSource = resolve; }))
      .mockResolvedValue({ batch: makeBatch({
        ingestionMode: "transfer-variant", revision: 22, items: [finalItem],
      }) });
    mocks.editVariationCandidate.mockResolvedValue({ batch: makeBatch({
      ingestionMode: "transfer-variant", revision: 23, items: [finalItem],
    }) });
    await renderAtReviewStep({ batch: makeBatch({
      ingestionMode: "transfer-variant", revision: 20, items: [initialItem],
    }) });

    fireEvent.click(screen.getByTestId("bulk-review-edit-source"));
    fireEvent.change(screen.getByTestId("bulk-review-tags-field"), { target: { value: "algebra" } });
    fireEvent.keyDown(screen.getByTestId("bulk-review-tags-field"), { key: "Enter", code: "Enter" });
    await act(async () => { vi.advanceTimersByTime(600); });
    expect(mocks.updateItemDraft).toHaveBeenCalledTimes(1);

    if (change === "add") {
      fireEvent.change(screen.getByTestId("bulk-review-tags-field"), { target: { value: "geometry" } });
      fireEvent.keyDown(screen.getByTestId("bulk-review-tags-field"), { key: "Enter", code: "Enter" });
    } else {
      fireEvent.click(screen.getByTestId("bulk-review-tags-remove-algebra"));
    }
    await act(async () => { resolveFirstSource({ batch: makeBatch({
      ingestionMode: "transfer-variant", revision: 21, items: [firstSaved],
    }) }); });

    // The second edit is still unsaved when the first response reaches both views.
    for (const tag of pendingTags) {
      expect(screen.getByTestId(`bulk-review-tags-tag-${tag}`)).toBeInTheDocument();
    }
    if (change === "undo") {
      expect(screen.queryByTestId("bulk-review-tags-tag-algebra")).not.toBeInTheDocument();
    }
    fireEvent.click(screen.getByTestId("bulk-review-edit-candidate"));
    for (const tag of pendingTags) {
      expect(screen.getByTestId(`bulk-review-tags-tag-${tag}`)).toBeInTheDocument();
    }
    if (change === "undo") {
      expect(screen.queryByTestId("bulk-review-tags-tag-algebra")).not.toBeInTheDocument();
    }
    fireEvent.change(screen.getByTestId("bulk-review-tags-field"), { target: { value: "trigonometry" } });
    fireEvent.keyDown(screen.getByTestId("bulk-review-tags-field"), { key: "Enter", code: "Enter" });
    await act(async () => { vi.advanceTimersByTime(1200); });
    await waitFor(() => { expect(mocks.editVariationCandidate).toHaveBeenCalled(); });
    expect(mocks.editVariationCandidate).toHaveBeenLastCalledWith(
      "batch-1", "item-1", expect.objectContaining({ tags: finalTags }),
    );
    expect(screen.queryByTestId("bulk-review-save-status")).not.toBeInTheDocument();
  });

  it("accepts the source edit that began after polling saw this page's candidate save", async () => {
    let resolveCandidate: (value: BatchResponse) => void = () => undefined;
    const initialItem = readyVariantItem();
    const extractingItem = makeItem({ itemId: "item-2", status: "extracting", order: 1 });
    const candidateSaved = readyVariantItem({
      contentRevision: 11,
      updatedAt: "2026-07-03T00:00:01Z",
      variation: {
        ...initialItem.variation!,
        status: "needs-validation",
        candidate: { ...initialItem.variation!.candidate!, correctAnswer: "66" },
        validation: null,
        validatedRevision: null,
      },
    });
    const observed = makeBatch({
      ingestionMode: "transfer-variant", revision: 21,
      items: [candidateSaved, extractingItem],
    });
    const tagsSaved = {
      ...candidateSaved,
      draft: { ...candidateSaved.draft, tags: ["math", "algebra"] },
      updatedAt: "2026-07-03T00:00:02Z",
    };
    mocks.editVariationCandidate
      .mockImplementationOnce(() => new Promise((resolve) => { resolveCandidate = resolve; }))
      .mockResolvedValue({ batch: makeBatch({
        ingestionMode: "transfer-variant", revision: 22,
        items: [tagsSaved, extractingItem],
      }) });
    mocks.updateItemDraft.mockResolvedValue({ batch: makeBatch({
      ingestionMode: "transfer-variant", revision: 23,
      items: [{ ...tagsSaved, updatedAt: "2026-07-03T00:00:03Z" }, extractingItem],
    }) });
    await renderAtReviewStep({ batch: makeBatch({
      ingestionMode: "transfer-variant", revision: 20,
      items: [initialItem, extractingItem],
    }) });

    fireEvent.change(screen.getByTestId("bulk-review-answer"), { target: { value: "66" } });
    await act(async () => { vi.advanceTimersByTime(600); });
    expect(mocks.editVariationCandidate).toHaveBeenCalledTimes(1);
    mocks.getBatch.mockResolvedValue({ batch: observed });
    await act(async () => { vi.advanceTimersByTime(2500); });
    expect(mocks.getBatch).toHaveBeenCalledTimes(2);
    fireEvent.click(screen.getByTestId("bulk-review-edit-source"));
    fireEvent.change(screen.getByTestId("bulk-review-tags-field"), { target: { value: "algebra" } });
    fireEvent.keyDown(screen.getByTestId("bulk-review-tags-field"), { key: "Enter", code: "Enter" });

    await act(async () => { resolveCandidate({ batch: observed }); });
    expect(screen.queryByTestId("bulk-review-save-status")).not.toBeInTheDocument();
    await act(async () => { vi.advanceTimersByTime(600); });
    await act(async () => { vi.advanceTimersByTime(600); });
    expect(mocks.updateItemDraft).toHaveBeenCalledWith(
      "batch-1", "item-1", expect.objectContaining({ tags: ["math", "algebra"] }), 11,
    );
    expect(screen.queryByTestId("bulk-review-save-status")).not.toBeInTheDocument();
    fireEvent.click(screen.getByTestId("bulk-review-edit-candidate"));
    expect(screen.getByTestId("bulk-review-answer")).toHaveValue("66");
  });

  it("routes Generate to the generate endpoint with the reviewed source and expectedRevision", async () => {
    const variantItem = makeItem({ contentRevision: 5 });
    const variantBatch = makeBatch({
      ingestionMode: "transfer-variant",
      items: [variantItem],
    });
    mocks.generateVariation.mockResolvedValue({ batch: variantBatch });

    await renderAtReviewStep({ batch: variantBatch });

    fireEvent.click(screen.getByTestId("bulk-review-generate"));

    await waitFor(() => {
      expect(mocks.generateVariation).toHaveBeenCalledTimes(1);
    });
    expect(mocks.generateVariation).toHaveBeenCalledWith("batch-1", "item-1", {
      expectedRevision: 5,
      original: expect.objectContaining({
        text: "What is 2+2?",
        problemType: "short-answer",
        correctAnswer: "4",
      }),
    });
  });

  it("routes Revalidate to the validator-only endpoint with the candidate revision, never generate", async () => {
    const itemNeedingValidation = makeItem({
      contentRevision: 4,
      variation: {
        status: "needs-validation",
        generationCount: 1,
        original: {
          text: "What is 2+2?",
          problemType: "short-answer",
          graphDsl: "",
          correctAnswer: "4",
          subject: "math",
        },
        candidate: {
          text: "What is 3+3?",
          problemType: "short-answer",
          graphDsl: "",
          correctAnswer: "6",
        },
        validation: null,
        validatedRevision: null,
        attestation: null,
        queuedAt: null,
      },
    });
    const variantBatch = makeBatch({
      ingestionMode: "transfer-variant",
      items: [itemNeedingValidation],
    });
    mocks.revalidateVariation.mockResolvedValue({ batch: variantBatch });

    await renderAtReviewStep({ batch: variantBatch });

    expect(screen.getByTestId("bulk-review-continue")).toBeDisabled();
    fireEvent.click(screen.getByTestId("bulk-review-revalidate"));

    await waitFor(() => {
      expect(mocks.revalidateVariation).toHaveBeenCalledTimes(1);
    });
    expect(mocks.revalidateVariation).toHaveBeenCalledWith(
      "batch-1",
      "item-1",
      4,
    );
    expect(mocks.generateVariation).not.toHaveBeenCalled();
  });
});
