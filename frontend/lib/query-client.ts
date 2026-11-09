import { QueryClient } from "@tanstack/react-query";

import { ApiError, NetworkError } from "@/lib/api/errors";

/** Reads retry network failures and 5xx responses twice; client errors (4xx) never. */
export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
  if (failureCount >= 2) return false;
  if (error instanceof NetworkError) return true;
  return error instanceof ApiError && error.status >= 500;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 15_000,
        retry: shouldRetryQuery,
        retryDelay: (attempt) => Math.min(1000 * 2 ** attempt, 8000),
      },
      // Writes are never retried automatically: the user decides whether to try again.
      mutations: { retry: false },
    },
  });
}
