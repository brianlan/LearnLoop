import { describe, it, expect, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { IngestPage } from "./IngestPage";

vi.mock("@/hooks/useTagSuggestions", () => ({
  useTagSuggestions: () => [],
}));

const mockNavigate = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual("react-router-dom");
  return {
    ...actual,
    useNavigate: () => mockNavigate,
  };
});

describe("IngestPage", () => {
  beforeEach(() => {
    mockNavigate.mockClear();
  });

  it("renders the page title", () => {
    render(
      <MemoryRouter>
        <IngestPage />
      </MemoryRouter>
    );
    expect(screen.getByText("Ingest New Problem")).toBeInTheDocument();
  });

  it("renders the IngestionWizard component", () => {
    render(
      <MemoryRouter>
        <IngestPage />
      </MemoryRouter>
    );
    expect(screen.getByTestId("ingestion-wizard")).toBeInTheDocument();
  });

  it("renders wizard in the main content area", () => {
    const { container } = render(
      <MemoryRouter>
        <IngestPage />
      </MemoryRouter>
    );
    const main = container.querySelector("main");
    expect(main).toBeInTheDocument();
  });

  it("has correct page heading level", () => {
    render(
      <MemoryRouter>
        <IngestPage />
      </MemoryRouter>
    );
    const heading = screen.getByRole("heading", { level: 1 });
    expect(heading).toHaveTextContent("Ingest New Problem");
  });
});
