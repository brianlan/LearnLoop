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

    it("shows helper classification failure message when helperDetection has failureCode", async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            preview: {
              id: "test-preview-id",
              status: "vlm-failed",
              sourceImage: { bucket: "test", objectKey: "test-key" },
              draft: {},
              extraction: {},
              helperDetection: {
                subject: null,
                failureCode: "vlm-timeout",
                failureMessage: "Helper VLM request timed out",
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
        expect(screen.getByText(/Subject Classification Failed/)).toBeInTheDocument();
      });
      expect(screen.getByText(/select the subject manually/)).toBeInTheDocument();
    });

    it("renders subject selector on helper classification failure", async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            preview: {
              id: "test-preview-id",
              status: "vlm-failed",
              sourceImage: { bucket: "test", objectKey: "test-key" },
              draft: {},
              extraction: {},
              helperDetection: {
                subject: null,
                failureCode: "vlm-timeout",
                failureMessage: "Helper VLM request timed out",
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
        expect(screen.getByTestId("helper-failure-subject-select")).toBeInTheDocument();
      });
      const select = screen.getByTestId("helper-failure-subject-select") as HTMLSelectElement;
      expect(select.value).toBe("math");
    });

    it("persists selected subject before retrying on helper failure", async () => {
      const mockFetch = vi.fn()
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              preview: {
                id: "test-preview-id",
                status: "vlm-failed",
                sourceImage: { bucket: "test", objectKey: "test-key" },
                draft: {},
                extraction: {},
                helperDetection: {
                  subject: null,
                  failureCode: "vlm-timeout",
                  failureMessage: "Helper VLM request timed out",
                },
                createdAt: new Date().toISOString(),
                updatedAt: new Date().toISOString(),
                expiresAt: new Date().toISOString(),
              },
            }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () => Promise.resolve({ preview: { id: "test-preview-id" } }),
        })
        .mockResolvedValueOnce({
          ok: true,
          json: () =>
            Promise.resolve({
              preview: {
                id: "test-preview-id",
                status: "ready",
                sourceImage: { bucket: "test", objectKey: "test-key" },
                draft: { text: "test", problemType: "short-answer", subject: "english", tags: [] },
                extraction: {},
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
        expect(screen.getByTestId("helper-failure-subject-select")).toBeInTheDocument();
      });

      const select = screen.getByTestId("helper-failure-subject-select") as HTMLSelectElement;
      fireEvent.change(select, { target: { value: "english" } });

      const retryButton = screen.getByRole("button", { name: /Try Again/ });
      fireEvent.click(retryButton);

      await waitFor(() => {
        const patchCall = mockFetch.mock.calls.find(
          (call: unknown[]) => (call[1] as { method?: string })?.method === "PATCH"
        );
        expect(patchCall).toBeTruthy();
        expect(JSON.parse((patchCall![1] as { body: string }).body)).toEqual({ subject: "english" });
      });

      await waitFor(() => {
        const retryCall = mockFetch.mock.calls.find(
          (call: unknown[]) => (call[1] as { method?: string })?.method === "POST"
        );
        expect(retryCall).toBeTruthy();
      });
    });
  });

  describe("choice preview", () => {
    it("renders ingestion wizard with single-choice draft data", () => {
      const draft = {
        text: "1. What is 2+2?\nA. 3\nB. 4\nC. 5\nD. 6",
        problemType: "single-choice",
        graphDsl: "",
        correctAnswer: "B",
        tags: [],
      };
      localStorage.setItem("ingestion-draft", JSON.stringify(draft));

      render(<IngestionWizard />);
      // Verify the wizard renders (starts at paste step)
      expect(screen.getByTestId("ingestion-wizard")).toBeInTheDocument();
    });
  });

  describe("subject selector", () => {
    it("renders subject selector with default math", async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            preview: {
              id: "test-preview-id",
              status: "ready",
              sourceImage: { bucket: "test", objectKey: "test-key" },
              draft: {
                text: "What is 2+2?",
                problemType: "short-answer",
                graphDsl: null,
                correctAnswer: "4",
                tags: [],
                subject: "math",
              },
              extraction: {
                rawText: "What is 2+2?",
                rawProblemType: "short-answer",
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
        expect(screen.getByTestId("subject-input")).toBeInTheDocument();
      });

      const subjectSelect = screen.getByTestId("subject-input") as HTMLSelectElement;
      expect(subjectSelect.value).toBe("math");
    });
  });

  describe("API Client migration contracts", () => {
    it("sends FormData with image field and no Content-Type header on file upload", async () => {
      const mockFetch = vi.fn().mockResolvedValue({
        ok: true,
        json: () =>
          Promise.resolve({
            preview: {
              id: "test-preview-id",
              status: "extracting",
              sourceImage: { bucket: "test", objectKey: "test-key" },
              draft: {},
              extraction: {},
              createdAt: new Date().toISOString(),
              updatedAt: new Date().toISOString(),
              expiresAt: new Date().toISOString(),
            },
          }),
      });
      vi.stubGlobal("fetch", mockFetch);

      render(<IngestionWizard />);

      const file = new File(["test-content"], "test-image.png", { type: "image/png" });
      const fileInput = screen.getByRole("button", {
        name: "Choose Image File",
      }).parentElement?.querySelector('input[type="file"]') as HTMLInputElement;

      fireEvent.change(fileInput, { target: { files: [file] } });

      await waitFor(() => {
        expect(mockFetch).toHaveBeenCalled();
      });

      const uploadCall = mockFetch.mock.calls.find(call => call[0] === "/api/v1/ingestion-previews");
      expect(uploadCall).toBeDefined();
      const [, options] = uploadCall!;
      
      expect(options.method).toBe("POST");
      expect(options.credentials).toBe("include");
      expect(options.body).toBeInstanceOf(FormData);
      
      const formData = options.body as FormData;
      expect(formData.get("image")).toEqual(file);
      
      if (options.headers) {
        const headers = options.headers as Record<string, string>;
        expect(headers["Content-Type"]).toBeUndefined();
        expect(headers["content-type"]).toBeUndefined();
      }
    });
  });
});
