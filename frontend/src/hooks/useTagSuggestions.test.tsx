import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { renderHook, waitFor } from "@testing-library/react";
import { describe, it, expect, vi, beforeEach } from "vitest";
import type { ReactNode } from "react";

import { useTagSuggestions } from "./useTagSuggestions";

const mockFetch = vi.fn();
global.fetch = mockFetch;

function createWrapper(queryClient: QueryClient) {
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
  };
}

describe("useTagSuggestions", () => {
  beforeEach(() => {
    mockFetch.mockReset();
  });

  it("returns string[] even when TagsPage cache is already populated", async () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    // Simulate TagsPage having already cached raw TagsResponse under ["tags"]
    queryClient.setQueryData(["tags"], {
      items: [
        { id: "t1", name: "algebra", createdAt: "", problemCount: 3 },
        { id: "t2", name: "geometry", createdAt: "", problemCount: 1 },
      ],
    });

    // Mock the /tags API call that useTagSuggestions will make
    mockFetch.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        items: [
          { id: "t1", name: "algebra", createdAt: "", problemCount: 3 },
          { id: "t2", name: "geometry", createdAt: "", problemCount: 1 },
        ],
      }),
    });

    const { result } = renderHook(() => useTagSuggestions(), {
      wrapper: createWrapper(queryClient),
    });

    await waitFor(() => {
      expect(result.current.length).toBeGreaterThan(0);
    });

    // The critical assertion: must be a string[], not a TagsResponse object
    expect(Array.isArray(result.current)).toBe(true);
    expect(result.current).toEqual(["algebra", "geometry"]);
    expect(typeof result.current[0]).toBe("string");
  });
});
