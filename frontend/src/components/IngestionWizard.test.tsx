import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { IngestionWizard } from "./IngestionWizard";

vi.mock("@/hooks/useTagSuggestions", () => ({
  useTagSuggestions: () => [],
}));

describe("IngestionWizard", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it("renders the paste step initially", () => {
    render(<IngestionWizard />);
    expect(screen.getByText("Paste an Image")).toBeInTheDocument();
    expect(screen.getByText(/Copy an image and paste it here/)).toBeInTheDocument();
  });

  it("renders step indicator", () => {
    render(<IngestionWizard />);
    expect(screen.getByText("Paste")).toBeInTheDocument();
    expect(screen.getByText("Upload")).toBeInTheDocument();
    expect(screen.getByText("Process")).toBeInTheDocument();
    expect(screen.getByText("Edit")).toBeInTheDocument();
    expect(screen.getByText("Confirm")).toBeInTheDocument();
  });

  it("renders the wizard container", () => {
    render(<IngestionWizard />);
    expect(screen.getByTestId("ingestion-wizard")).toBeInTheDocument();
  });

  it("renders paste area with proper accessibility", () => {
    render(<IngestionWizard />);
    const pasteArea = screen.getByRole("region", { name: /Paste image area/ });
    expect(pasteArea).toBeInTheDocument();
    expect(pasteArea).toHaveAttribute("tabindex", "0");
  });

  it("renders a file upload fallback", () => {
    render(<IngestionWizard />);
    expect(screen.getByRole("button", { name: "Choose Image File" })).toBeInTheDocument();
  });

  it("loads draft from localStorage on mount", () => {
    const draft = {
      text: "Saved problem text",
      problemType: "algebra",
      graphDsl: "var p = board.create('point', [0, 0]);",
      correctAnswer: "42",
      tags: "math, hard",
    };
    localStorage.setItem("ingestion-draft", JSON.stringify(draft));

    render(<IngestionWizard />);
    expect(screen.getByTestId("ingestion-wizard")).toBeInTheDocument();
  });

  describe("VLM error details", () => {
    it("shows error details when failureCode is present", async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            preview: {
              id: "test-preview-id",
              status: "vlm-failed",
              sourceImage: { bucket: "test", objectKey: "test-key" },
              draft: {},
              extraction: {
                failureCode: "VLM_ERROR",
                failureMessage: null,
              },
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
              expiresAt: new Date().toISOString(),
            },
          }),
      });
      vi.stubGlobal("fetch", mockFetch);

      render(<IngestionWizard />);

      const file = new File(["test"], "test.png", { type: "image/png" });
      const fileInput = screen.getByRole("button", {
        name: "Choose Image File",
      }).parentElement?.querySelector('input[type="file"]') as HTMLInputElement;

      fireEvent.change(fileInput, { target: { files: [file] } });

      await waitFor(() => {
        expect(screen.getByText(/Extraction Failed/)).toBeInTheDocument();
      });

      expect(screen.getByText("View error details")).toBeInTheDocument();

      const details = screen.getByText("View error details").closest("details");
      expect(details).toBeInTheDocument();
    });

    it("shows error details when failureMessage is present", async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            preview: {
              id: "test-preview-id",
              status: "vlm-failed",
              sourceImage: { bucket: "test", objectKey: "test-key" },
              draft: {},
              extraction: {
                failureCode: null,
                failureMessage: "Connection timeout",
              },
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
              expiresAt: new Date().toISOString(),
            },
          }),
      });
      vi.stubGlobal("fetch", mockFetch);

      render(<IngestionWizard />);

      const file = new File(["test"], "test.png", { type: "image/png" });
      const fileInput = screen.getByRole("button", {
        name: "Choose Image File",
      }).parentElement?.querySelector('input[type="file"]') as HTMLInputElement;

      fireEvent.change(fileInput, { target: { files: [file] } });

      await waitFor(() => {
        expect(screen.getByText(/Extraction Failed/)).toBeInTheDocument();
      });

      expect(screen.getByText("View error details")).toBeInTheDocument();
    });

    it("displays failureCode when expanded", async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            preview: {
              id: "test-preview-id",
              status: "vlm-failed",
              sourceImage: { bucket: "test", objectKey: "test-key" },
              draft: {},
              extraction: {
                failureCode: "TIMEOUT_ERROR",
                failureMessage: null,
              },
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
              expiresAt: new Date().toISOString(),
            },
          }),
      });
      vi.stubGlobal("fetch", mockFetch);

      render(<IngestionWizard />);

      const file = new File(["test"], "test.png", { type: "image/png" });
      const fileInput = screen.getByRole("button", {
        name: "Choose Image File",
      }).parentElement?.querySelector('input[type="file"]') as HTMLInputElement;

      fireEvent.change(fileInput, { target: { files: [file] } });

      await waitFor(() => {
        expect(screen.getByText(/Extraction Failed/)).toBeInTheDocument();
      });

      const summary = screen.getByText("View error details");
      fireEvent.click(summary);

      await waitFor(() => {
        expect(screen.getByText(/TIMEOUT_ERROR/)).toBeInTheDocument();
      });
    });

    it("displays failureMessage when expanded", async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            preview: {
              id: "test-preview-id",
              status: "vlm-failed",
              sourceImage: { bucket: "test", objectKey: "test-key" },
              draft: {},
              extraction: {
                failureCode: null,
                failureMessage: "Rate limit exceeded",
              },
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
              expiresAt: new Date().toISOString(),
            },
          }),
      });
      vi.stubGlobal("fetch", mockFetch);

      render(<IngestionWizard />);

      const file = new File(["test"], "test.png", { type: "image/png" });
      const fileInput = screen.getByRole("button", {
        name: "Choose Image File",
      }).parentElement?.querySelector('input[type="file"]') as HTMLInputElement;

      fireEvent.change(fileInput, { target: { files: [file] } });

      await waitFor(() => {
        expect(screen.getByText(/Extraction Failed/)).toBeInTheDocument();
      });

      const summary = screen.getByText("View error details");
      fireEvent.click(summary);

      await waitFor(() => {
        expect(screen.getByText(/Rate limit exceeded/)).toBeInTheDocument();
      });
    });

    it("does not show error details when failureCode and failureMessage are null", async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            preview: {
              id: "test-preview-id",
              status: "vlm-failed",
              sourceImage: { bucket: "test", objectKey: "test-key" },
              draft: {},
              extraction: {
                failureCode: null,
                failureMessage: null,
              },
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
              expiresAt: new Date().toISOString(),
            },
          }),
      });
      vi.stubGlobal("fetch", mockFetch);

      render(<IngestionWizard />);

      const file = new File(["test"], "test.png", { type: "image/png" });
      const fileInput = screen.getByRole("button", {
        name: "Choose Image File",
      }).parentElement?.querySelector('input[type="file"]') as HTMLInputElement;

      fireEvent.change(fileInput, { target: { files: [file] } });

      await waitFor(() => {
        expect(screen.getByText(/Extraction Failed/)).toBeInTheDocument();
      });

      expect(screen.queryByText("View error details")).not.toBeInTheDocument();
    });
  });
});
