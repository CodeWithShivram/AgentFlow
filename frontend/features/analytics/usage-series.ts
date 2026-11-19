import type { MetricRate, TokenRow } from "@/lib/api/types";

export interface UsageBucket {
  /** First and last local day of the bucket, as YYYY-MM-DD. */
  start: string;
  end: string;
  calls: number;
  inputTokens: number;
  outputTokens: number;
}

/** The viewer's IANA time zone, which cuts the days (ADR-0045). */
export function viewerTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
}

/** YYYY-MM-DD of an instant in a time zone. */
export function localDay(instant: Date, timeZone: string): string {
  // en-CA formats dates as YYYY-MM-DD.
  return new Intl.DateTimeFormat("en-CA", { timeZone, year: "numeric", month: "2-digit", day: "2-digit" }).format(
    instant,
  );
}

/** Every calendar day from `first` to `last` inclusive (YYYY-MM-DD, computed in UTC to avoid DST gaps). */
export function daysBetween(first: string, last: string): string[] {
  const days: string[] = [];
  const end = Date.parse(`${last}T00:00:00Z`);
  for (let t = Date.parse(`${first}T00:00:00Z`); t <= end; t += 86_400_000) {
    days.push(new Date(t).toISOString().slice(0, 10));
  }
  return days;
}

/**
 * One bucket per day of the range, or per 7 days counted back from the last day when the range is
 * longer than a month. A day without calls is a real zero: nothing was recorded.
 */
export function usageBuckets(rows: TokenRow[], days: string[]): UsageBucket[] {
  const byDay = new Map(rows.map((r) => [r.key, r]));
  const daily: UsageBucket[] = days.map((day) => {
    const row = byDay.get(day);
    return {
      start: day,
      end: day,
      calls: row?.calls ?? 0,
      inputTokens: row?.input_tokens ?? 0,
      outputTokens: row?.output_tokens ?? 0,
    };
  });
  if (daily.length <= 31) return daily;
  const weeks: UsageBucket[] = [];
  for (let end = daily.length; end > 0; end -= 7) {
    const chunk = daily.slice(Math.max(0, end - 7), end);
    weeks.unshift({
      start: chunk[0]!.start,
      end: chunk[chunk.length - 1]!.end,
      calls: chunk.reduce((n, b) => n + b.calls, 0),
      inputTokens: chunk.reduce((n, b) => n + b.inputTokens, 0),
      outputTokens: chunk.reduce((n, b) => n + b.outputTokens, 0),
    });
  }
  return weeks;
}

/** A clean axis maximum at or above `value`: 1, 2 or 5 times a power of ten. */
export function niceMax(value: number): number {
  if (value <= 0) return 1;
  const power = 10 ** Math.floor(Math.log10(value));
  for (const step of [1, 2, 5, 10]) {
    if (step * power >= value) return step * power;
  }
  return 10 * power;
}

/** Attempts that succeeded over finished attempts that were not cancelled; null when none finished. */
export function attemptSuccessRate(byStatus: Record<string, number>, failureRate: MetricRate): MetricRate {
  const denominator = failureRate.denominator;
  const numerator = byStatus.SUCCEEDED ?? 0;
  return { numerator, denominator, value: denominator === 0 ? null : numerator / denominator };
}
