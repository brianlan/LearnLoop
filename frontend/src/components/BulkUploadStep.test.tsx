import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { BulkUploadStep } from "./BulkUploadStep";
import type { BulkBatch } from "@/types/bulkIngestion";

function makeBatch(overrides: Partial<BulkBatch> = {}): BulkBatch {
  return {
    id: "batch-1",
    userId: "user-1",
    status: "active",
    ingestionMode: "original",
    images: [],
    items: [],
    createdAt: "2026-07-03T00:00:00Z",
    updatedAt: "2026-07-03T00:00:00Z",
    expiresAt: "2026-07-04T00:00:00Z",
    ...overrides,
  };
}

describe("BulkUploadStep", () => {
  it("accepts both images and PDFs", () => {
    render(
      <BulkUploadStep
        batch={makeBatch()}
        isLoading={false}
        mode="original"
        onModeChange={() => {}}
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
        mode="original"
        onModeChange={() => {}}
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
        mode="original"
        onModeChange={() => {}}
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

  it("shows the mode radio group before batch creation with Original preselected", () => {
    render(
      <BulkUploadStep
        batch={null}
        isLoading={false}
        mode="original"
        onModeChange={() => {}}
        onCreateBatch={() => {}}
        onUpload={() => {}}
      />,
    );

    const group = screen.getByRole("group", { name: /ingestion mode/i });
    expect(group).toBeInTheDocument();

    const original = screen.getByTestId(
      "bulk-wizard-mode-original",
    ) as HTMLInputElement;
    const dataOnly = screen.getByTestId(
      "bulk-wizard-mode-data-only",
    ) as HTMLInputElement;
    const dataAndWording = screen.getByTestId(
      "bulk-wizard-mode-data-and-wording",
    ) as HTMLInputElement;

    expect(original.checked).toBe(true);
    expect(dataOnly.checked).toBe(false);
    expect(dataAndWording.checked).toBe(false);
  });

  it("fires onModeChange when another mode is selected", () => {
    const onModeChange = vi.fn();
    render(
      <BulkUploadStep
        batch={null}
        isLoading={false}
        mode="original"
        onModeChange={onModeChange}
        onCreateBatch={() => {}}
        onUpload={() => {}}
      />,
    );

    fireEvent.click(screen.getByTestId("bulk-wizard-mode-data-only"));

    expect(onModeChange).toHaveBeenCalledTimes(1);
    expect(onModeChange).toHaveBeenCalledWith("data-only");
  });

  it("shows the batch mode as locked text instead of radios when a batch exists", () => {
    render(
      <BulkUploadStep
        batch={makeBatch({ ingestionMode: "data-only" })}
        isLoading={false}
        mode="original"
        onModeChange={() => {}}
        onCreateBatch={() => {}}
        onUpload={() => {}}
      />,
    );

    expect(screen.getByTestId("bulk-wizard-mode-locked")).toHaveTextContent(
      "Ingestion mode: Variant (data-only) (locked for this batch)",
    );
    expect(screen.queryByRole("group", { name: /ingestion mode/i })).not.toBeInTheDocument();
  });

  it("labels the mode radios with accessible descriptions", () => {
    render(
      <BulkUploadStep
        batch={null}
        isLoading={false}
        mode="original"
        onModeChange={() => {}}
        onCreateBatch={() => {}}
        onUpload={() => {}}
      />,
    );

    expect(
      screen.getByLabelText(/Original — Ingest the problems exactly as they are\./i),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText(
        /Variant \(data-only\) — Generate a new practice problem with different numbers/i,
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByLabelText(
        /Variant \(data-and-wording\) — Generate a new practice problem with different data/i,
      ),
    ).toBeInTheDocument();
  });
});
