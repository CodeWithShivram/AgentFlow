"use client";

import { useQuery } from "@tanstack/react-query";

import { api } from "@/lib/api";

/** Platform policy (capabilities, categories, tools); identical for every workspace. */
export function useCatalog() {
  return useQuery({
    queryKey: ["agent-catalog"],
    queryFn: () => api.agents.catalog(),
    staleTime: Infinity,
  });
}
