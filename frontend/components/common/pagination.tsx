"use client";

import { ChevronLeftIcon, ChevronRightIcon } from "lucide-react";

import { Button } from "@/components/ui/button";

interface PaginationProps {
  offset: number;
  limit: number;
  total: number;
  onChange(offset: number): void;
  disabled?: boolean;
}

export function Pagination({ offset, limit, total, onChange, disabled }: PaginationProps) {
  if (total <= limit && offset === 0) return null;
  const first = total === 0 ? 0 : offset + 1;
  const last = Math.min(offset + limit, total);
  return (
    <nav className="flex items-center justify-between gap-2 pt-2" aria-label="Pagination">
      <p className="text-sm text-muted-foreground" aria-live="polite">
        {first}–{last} of {total}
      </p>
      <div className="flex gap-2">
        <Button
          variant="outline"
          size="sm"
          onClick={() => onChange(Math.max(0, offset - limit))}
          disabled={disabled || offset === 0}
        >
          <ChevronLeftIcon data-icon="inline-start" />
          Previous
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => onChange(offset + limit)}
          disabled={disabled || offset + limit >= total}
        >
          Next
          <ChevronRightIcon data-icon="inline-end" />
        </Button>
      </div>
    </nav>
  );
}
