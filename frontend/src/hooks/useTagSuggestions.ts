import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";
import type { TagsResponse } from "@/types/tag";

export function useTagSuggestions() {
  const { data } = useQuery({
    queryKey: ["tags"],
    queryFn: async () => {
      const response = await api.get<TagsResponse>("/tags");
      return response.items?.map((item) => item.name) ?? [];
    },
    staleTime: 30_000,
  });

  return data ?? [];
}
