import { useQuery } from "@tanstack/react-query";
import { api } from "@/api/client";

interface TagsResponse {
  items: string[];
}

export function useTagSuggestions() {
  const { data } = useQuery({
    queryKey: ["problem-tags"],
    queryFn: async () => {
      const response = await api.get<TagsResponse>("/problems/tags");
      return response.items;
    },
    staleTime: 30_000,
  });

  return data ?? [];
}
