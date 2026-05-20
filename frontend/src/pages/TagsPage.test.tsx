import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, it, expect, vi, beforeEach } from "vitest";

import { TagsPage } from "./TagsPage";

const mockFetch = vi.fn();
global.fetch = mockFetch;

function createQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false },
    },
  });
}

function renderTagsPage() {
  return render(
    <QueryClientProvider client={createQueryClient()}>
      <MemoryRouter>
        <TagsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

describe("TagsPage", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("renders empty state when no tags", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({ items: [] }),
    });

    renderTagsPage();

    await waitFor(() => {
      expect(screen.getByTestId("empty-state")).toBeInTheDocument();
    });
    expect(screen.getByText("No tags yet.")).toBeInTheDocument();
  });

  it("renders list of tags with problem counts", async () => {
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        items: [
          { id: "t1", name: "algebra", createdAt: "2024-01-01", problemCount: 5 },
          { id: "t2", name: "geometry", createdAt: "2024-01-02", problemCount: 3 },
        ],
      }),
    });

    renderTagsPage();

    await waitFor(() => {
      expect(screen.getByText("algebra")).toBeInTheDocument();
    });
    expect(screen.getByText("geometry")).toBeInTheDocument();
    expect(screen.getByText("5 problems")).toBeInTheDocument();
    expect(screen.getByText("3 problems")).toBeInTheDocument();
  });

  it("creates a new tag", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [] }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tag: { id: "t1", name: "calculus", createdAt: "2024-01-01", problemCount: 0 },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          items: [{ id: "t1", name: "calculus", createdAt: "2024-01-01", problemCount: 0 }],
        }),
      });

    renderTagsPage();

    await waitFor(() => {
      expect(screen.getByTestId("empty-state")).toBeInTheDocument();
    });

    await user.type(screen.getByTestId("new-tag-input"), "calculus");
    await user.click(screen.getByTestId("add-tag-button"));

    await waitFor(() => {
      expect(screen.getByText("calculus")).toBeInTheDocument();
    });
  });

  it("shows error when creating duplicate tag", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [] }),
      })
      .mockResolvedValueOnce({
        ok: false,
        json: async () => ({ error: { code: "DUPLICATE_TAG", message: "Tag with this name already exists" } }),
      });

    renderTagsPage();

    await waitFor(() => {
      expect(screen.getByTestId("empty-state")).toBeInTheDocument();
    });

    await user.type(screen.getByTestId("new-tag-input"), "duplicate");
    await user.click(screen.getByTestId("add-tag-button"));

    await waitFor(() => {
      expect(screen.getByTestId("tag-error")).toBeInTheDocument();
    });
    expect(screen.getByText("Tag with this name already exists")).toBeInTheDocument();
  });

  it("renames a tag", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          items: [{ id: "t1", name: "algebra", createdAt: "2024-01-01", problemCount: 5 }],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          tag: { id: "t1", name: "algebra-ii", createdAt: "2024-01-01", problemCount: 5 },
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          items: [{ id: "t1", name: "algebra-ii", createdAt: "2024-01-01", problemCount: 5 }],
        }),
      });

    renderTagsPage();

    await waitFor(() => {
      expect(screen.getByText("algebra")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("edit-tag-button-t1"));
    await user.clear(screen.getByTestId("edit-tag-input-t1"));
    await user.type(screen.getByTestId("edit-tag-input-t1"), "algebra-ii");
    await user.click(screen.getByTestId("save-tag-button-t1"));

    await waitFor(() => {
      expect(screen.getByText("algebra-ii")).toBeInTheDocument();
    });
  });

  it("deletes a tag with confirmation", async () => {
    const user = userEvent.setup();
    mockFetch
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({
          items: [{ id: "t1", name: "algebra", createdAt: "2024-01-01", problemCount: 5 }],
        }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ ok: true }),
      })
      .mockResolvedValueOnce({
        ok: true,
        json: async () => ({ items: [] }),
      });

    renderTagsPage();

    await waitFor(() => {
      expect(screen.getByText("algebra")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("delete-tag-button-t1"));

    await waitFor(() => {
      expect(screen.getByTestId("delete-confirm-modal")).toBeInTheDocument();
    });
    expect(screen.getByText(/Are you sure you want to delete "algebra"/)).toBeInTheDocument();

    await user.click(screen.getByTestId("confirm-delete-button"));

    await waitFor(() => {
      expect(screen.getByTestId("empty-state")).toBeInTheDocument();
    });
  });

  it("cancels delete confirmation", async () => {
    const user = userEvent.setup();
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        items: [{ id: "t1", name: "algebra", createdAt: "2024-01-01", problemCount: 5 }],
      }),
    });

    renderTagsPage();

    await waitFor(() => {
      expect(screen.getByText("algebra")).toBeInTheDocument();
    });

    await user.click(screen.getByTestId("delete-tag-button-t1"));

    await waitFor(() => {
      expect(screen.getByTestId("delete-confirm-modal")).toBeInTheDocument();
    });

    await user.click(screen.getByText("Cancel"));

    await waitFor(() => {
      expect(screen.queryByTestId("delete-confirm-modal")).not.toBeInTheDocument();
    });
    expect(screen.getByText("algebra")).toBeInTheDocument();
  });
});
