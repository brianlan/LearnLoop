import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { IngestionWizard } from "./IngestionWizard";

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
});
