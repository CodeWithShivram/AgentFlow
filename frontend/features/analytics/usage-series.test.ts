import { describe, expect, it } from "vitest";

import type { TokenRow } from "@/lib/api/types";

import { attemptSuccessRate, daysBetween, localDay, niceMax, usageBuckets } from "./usage-series";

function row(key: string, calls: number, input = 0, output = 0): TokenRow {
  return { key, label: key, calls, input_tokens: input, output_tokens: output, cached_input_tokens: 0 };
}

describe("days", () => {
  it("cuts an instant into its local day", () => {
    const late = new Date("2026-11-10T23:30:00Z");
    expect(localDay(late, "UTC")).toBe("2026-11-10");
    expect(localDay(late, "Asia/Kolkata")).toBe("2026-11-11");
    expect(localDay(late, "America/New_York")).toBe("2026-11-10");
  });

  it("lists every calendar day, across month ends and DST changes", () => {
    expect(daysBetween("2026-10-30", "2026-11-02")).toEqual(["2026-10-30", "2026-10-31", "2026-11-01", "2026-11-02"]);
    expect(daysBetween("2026-03-28", "2026-03-30")).toHaveLength(3);
    expect(daysBetween("2026-11-02", "2026-11-02")).toEqual(["2026-11-02"]);
  });
});

describe("usageBuckets", () => {
  it("gives every day a bucket, with zero where nothing was recorded", () => {
    const buckets = usageBuckets([row("2026-11-02", 2, 100, 50)], daysBetween("2026-11-01", "2026-11-03"));
    expect(buckets.map((b) => [b.start, b.calls, b.inputTokens, b.outputTokens])).toEqual([
      ["2026-11-01", 0, 0, 0],
      ["2026-11-02", 2, 100, 50],
      ["2026-11-03", 0, 0, 0],
    ]);
  });

  it("groups past a month into weeks counted back from the last day, keeping every call", () => {
    const days = daysBetween("2026-08-21", "2026-11-19"); // 91 days
    const rows = [row("2026-08-21", 1, 10, 1), row("2026-11-13", 2, 20, 2), row("2026-11-19", 3, 30, 3)];
    const weeks = usageBuckets(rows, days);
    expect(weeks).toHaveLength(13);
    expect(weeks.at(-1)).toMatchObject({ start: "2026-11-13", end: "2026-11-19", calls: 5, inputTokens: 50 });
    expect(weeks[0]).toMatchObject({ start: "2026-08-21", calls: 1 });
    expect(weeks.reduce((n, w) => n + w.calls, 0)).toBe(6);
  });
});

describe("niceMax", () => {
  it("rounds up to 1, 2 or 5 times a power of ten", () => {
    expect([0, 1, 3, 7, 12, 4_500, 5_000, 50_001].map(niceMax)).toEqual([1, 1, 5, 10, 20, 5_000, 5_000, 100_000]);
  });
});

describe("attemptSuccessRate", () => {
  it("counts succeeded attempts over finished ones that were not cancelled", () => {
    const failure = { numerator: 1, denominator: 4, value: 0.25 };
    expect(attemptSuccessRate({ SUCCEEDED: 3, FAILED: 1, CANCELLED: 2 }, failure)).toEqual({
      numerator: 3,
      denominator: 4,
      value: 0.75,
    });
  });

  it("is not measured, never zero, when nothing finished", () => {
    const none = { numerator: 0, denominator: 0, value: null };
    expect(attemptSuccessRate({ CANCELLED: 1 }, none).value).toBeNull();
  });
});
