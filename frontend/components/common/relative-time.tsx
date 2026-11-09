"use client";

import { formatDateTime, formatRelative } from "@/lib/format";

export function RelativeTime({ value }: { value: string | null | undefined }) {
  if (!value) return <span>—</span>;
  return (
    <time dateTime={value} title={formatDateTime(value)}>
      {formatRelative(value)}
    </time>
  );
}
