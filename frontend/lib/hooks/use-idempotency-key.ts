import { useRef } from "react";

import { newIdempotencyKey } from "@/lib/format";

/**
 * Idempotency keys for a create form. Submitting the same request again (a retry after a
 * network error or a double click) reuses its key, so the server never creates it twice;
 * submitting a changed request gets a new key, because the server rightly refuses a key that
 * was already used for a different request (IDEMPOTENCY_KEY_REUSED).
 */
export function useIdempotencyKey(): (request: unknown) => string {
  const last = useRef<{ body: string; key: string } | null>(null);
  return (request: unknown) => {
    const body = JSON.stringify(request);
    if (last.current?.body !== body) {
      last.current = { body, key: newIdempotencyKey() };
    }
    return last.current.key;
  };
}
