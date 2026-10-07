import { describe, it, expect, vi, afterEach } from "vitest";
import { render, screen, cleanup, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { BulkDetectStep } from "./BulkDetectStep";
import type { BulkBatch, BulkImage } from "@/types/bulkIngestion";

function readyImage(overrides: Partial<BulkImage> = {}): BulkImage {
  return {
    imageId: "img-1",
    status: "ready",
    order: 0,
    sourceImage: { bucket: "b", objectKey: "k" },
    subject: "math",
    boxes: [],
    detection: {},
    createdAt: "2026-07-03T00:00:00Z",
    updatedAt: "2026-07-03T00:00:00Z",
    ...overrides,
  };
}

function makeBatch(images: BulkImage[]): BulkBatch {
  return {
    id: "batch-1",
    userId: "user-1",
    status: "active",
    ingestionMode: "original",
    images,
    items: [],
    createdAt: "2026-07-03T00:00:00Z",
    updatedAt: "2026-07-03T00:00:00Z",
    expiresAt: "2026-07-04T00:00:00Z",
  };
}

function renderStep(images: BulkImage[]) {
  const props = {
    batch: makeBatch(images),
    isLoading: false,
    onDetect: vi.fn(),
    onSaveBoxes: vi.fn(),
    onCommit: vi.fn(),
    onDelete: vi.fn(),
  };
  render(<BulkDetectStep {...props} />);
  return props;
}

describe("BulkDetectStep `s` shortcut", () => {
  // Test 3 appends a plain text input outside the component; always remove
  // it so it cannot leak focus into later tests.
  let strayInput: HTMLInputElement | null = null;

  afterEach(() => {
    strayInput?.remove();
    strayInput = null;
    cleanup();
  });

  it("saves the pending subject when `s` is pressed while the select is focused (#667)", async () => {
    const user = userEvent.setup();
    const props = renderStep([readyImage()]);
    const select = screen.getByTestId("bulk-detect-subject-img-1");
    await user.selectOptions(select, "english");
    // No blur: the select still holds DOM focus, which used to swallow
    // the shortcut.
    await user.keyboard("s");
    expect(props.onSaveBoxes).toHaveBeenCalledTimes(1);
    expect(props.onSaveBoxes).toHaveBeenCalledWith("img-1", [], "english");
    await waitFor(() =>
      expect(
        screen.queryByTestId("bulk-detect-save-img-1"),
      ).not.toBeInTheDocument(),
    );
  });

  it("still saves after focus has left the select", async () => {
    const user = userEvent.setup();
    const props = renderStep([readyImage()]);
    const select = screen.getByTestId("bulk-detect-subject-img-1");
    await user.selectOptions(select, "english");
    select.blur();
    await user.keyboard("s");
    expect(props.onSaveBoxes).toHaveBeenCalledTimes(1);
    expect(props.onSaveBoxes).toHaveBeenCalledWith("img-1", [], "english");
  });

  it("does not save while typing in a text input", async () => {
    const user = userEvent.setup();
    const props = renderStep([readyImage()]);
    await user.selectOptions(
      screen.getByTestId("bulk-detect-subject-img-1"),
      "english",
    );
    strayInput = document.createElement("input");
    document.body.appendChild(strayInput);
    strayInput.focus();
    await user.keyboard("s");
    expect(props.onSaveBoxes).not.toHaveBeenCalled();
  });
});
