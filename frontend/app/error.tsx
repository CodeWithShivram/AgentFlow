"use client";

import { Button } from "@/components/ui/button";

// Rendering errors only; API errors are handled where the data is used. No error details are
// shown: they could include internal information.
export default function Error({ reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main id="main-content" className="flex min-h-svh flex-col items-center justify-center gap-3 p-6 text-center">
      <h1 className="text-2xl font-semibold">Something went wrong</h1>
      <p className="text-sm text-muted-foreground">The page failed to display. Try again.</p>
      <Button onClick={reset}>Try again</Button>
    </main>
  );
}
