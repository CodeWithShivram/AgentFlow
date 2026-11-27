import { renderHook } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { useIdempotencyKey } from "./use-idempotency-key";

describe("useIdempotencyKey", () => {
  it("reuses the key for the same request and issues a new one when the request changes", () => {
    const { result } = renderHook(() => useIdempotencyKey());
    const keyFor = result.current;

    const first = keyFor({ prompt: "Build a CLI", kind: "SOFTWARE_PROJECT" });
    // A retry of the identical request (network error, double click) keeps its key.
    expect(keyFor({ prompt: "Build a CLI", kind: "SOFTWARE_PROJECT" })).toBe(first);
    // An edited request must not reuse it: the server refuses a key used for another request.
    const edited = keyFor({ prompt: "Build a better CLI", kind: "SOFTWARE_PROJECT" });
    expect(edited).not.toBe(first);
    expect(edited).toMatch(/^[0-9a-f-]{36}$/);
  });
});
