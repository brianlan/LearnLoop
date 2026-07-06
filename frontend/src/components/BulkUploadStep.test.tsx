import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { BulkUploadStep } from "./BulkUploadStep";
import type { BulkBatch } from "@/types/bulkIngestion";

function makeBatch(): BulkBatch {
  return {
    id: "batch-1",
    userId: "user-1",
    status: "active",
    images: [],
    items: [],
    createdAt: "2026-07-03T00:00:00Z",
    updatedAt: "2026-07-03T00:00:00Z",
    expiresAt: "2026-07-04T00:00:00Z",
  };
}

describe("BulkUploadStep", () => {
  it("accepts both images and PDFs", () => {
    render(
      <BulkUploadStep
        batch={makeBatch()}
        isLoading={false}
        onCreateBatch={() => {}}
        onUpload={() => {}}
      />,
    );

    const input = screen.getByTestId("bulk-wizard-upload-input") as HTMLInputElement;
    expect(input.accept).toContain("image/*");
    expect(input.accept).toContain("application/pdf");
    expect(input.accept).toContain(".pdf");
  });

  it("uses image-or-PDF wording in heading, description, and button", () => {
    render(
      <BulkUploadStep
        batch={makeBatch()}
        isLoading={false}
        onCreateBatch={() => {}}
        onUpload={() => {}}
      />,
    );

    expect(screen.getByText("Upload images or PDFs")).toBeInTheDocument();
    expect(screen.getByText("Add images or PDFs to your batch.")).toBeInTheDocument();
    expect(screen.getByTestId("bulk-wizard-upload-button").textContent).toBe(
      "Choose files",
    );
  });

  it("calls onUpload with selected files", () => {
    const onUpload = vi.fn();
    render(
      <BulkUploadStep
        batch={makeBatch()}
        isLoading={false}
        onCreateBatch={() => {}}
        onUpload={onUpload}
      />,
    );

    const input = screen.getByTestId("bulk-wizard-upload-input") as HTMLInputElement;
    const file = new File(["pdf"], "doc.pdf", { type: "application/pdf" });
    fireEvent.change(input, { target: { files: [file] } });

    expect(onUpload).toHaveBeenCalledTimes(1);
    const passedFiles = onUpload.mock.calls[0][0] as FileList;
    expect(passedFiles[0]).toBe(file);
  });
});
