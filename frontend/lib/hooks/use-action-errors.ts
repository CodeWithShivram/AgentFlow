"use client";

import { useCallback, useState } from "react";
import { toast } from "sonner";

import { ApiError, describeError } from "@/lib/api/errors";

/**
 * Handles a failed write: a revision conflict opens the conflict dialog (the caller reloads);
 * anything else becomes an error toast. Nothing is retried automatically.
 */
export function useActionErrors() {
  const [conflict, setConflict] = useState(false);
  const handle = useCallback((error: unknown) => {
    if (error instanceof ApiError && error.isRevisionConflict) {
      setConflict(true);
      return;
    }
    toast.error(describeError(error));
  }, []);
  const clearConflict = useCallback(() => setConflict(false), []);
  return { conflict, handle, clearConflict };
}
