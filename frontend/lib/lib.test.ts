import { describe, expect, it } from "vitest";

import { ApiError, NetworkError } from "./api/errors";
import { formatMoney, formatRelative, humanize } from "./format";
import { homePath, loginHref, safeNextPath } from "./navigation";
import { shouldRetryQuery } from "./query-client";
import { roleAtLeast } from "./roles";

const apiError = (status: number) =>
  new ApiError(status, { code: "X", message: "m", request_id: null, details: {} });

describe("safeNextPath", () => {
  it.each([
    ["/w/1/workflows", "/w/1/workflows"],
    ["/w/1/workflows?status=RUNNING", "/w/1/workflows?status=RUNNING"],
    ["//evil.example", null],
    ["/\\evil.example", null],
    ["https://evil.example", null],
    ["javascript:alert(1)", null],
    ["/login?next=/x", null],
    ["/register", null],
    ["/w/1\n/x", null],
    ["", null],
    [null, null],
  ])("%s -> %s", (input, expected) => {
    expect(safeNextPath(input)).toBe(expected);
  });

  it("builds login links that keep only safe targets", () => {
    expect(loginHref("/w/1/agents")).toBe("/login?next=%2Fw%2F1%2Fagents");
    expect(loginHref("//evil", true)).toBe("/login?expired=1");
    expect(loginHref(null)).toBe("/login");
  });

  it("chooses the first workspace when there is no target", () => {
    expect(homePath({ memberships: [{ workspace_id: "a", workspace_name: "A", role: "OWNER" }] })).toBe("/w/a/dashboard");
    expect(homePath({ memberships: [] })).toBe("/account");
    expect(homePath({ memberships: [] }, "/w/b/agents")).toBe("/w/b/agents");
  });
});

describe("format", () => {
  it("formats decimal strings with their currency", () => {
    expect(formatMoney("125.5000", "USD")).toContain("125.50");
    expect(formatMoney(null, "USD")).toBe("No limit");
  });

  it("humanizes enum values", () => {
    expect(humanize("AWAITING_APPROVAL")).toBe("Awaiting approval");
    expect(humanize("report_writing")).toBe("Report writing");
  });

  it("formats relative times", () => {
    const now = new Date("2026-01-01T12:00:00Z");
    expect(formatRelative("2026-01-01T11:59:58Z", now)).toBe("just now");
    expect(formatRelative("2026-01-01T11:00:00Z", now)).toMatch(/hour/);
    expect(formatRelative(null, now)).toBe("—");
  });
});

describe("shouldRetryQuery", () => {
  it("retries network failures and 5xx a bounded number of times", () => {
    expect(shouldRetryQuery(0, new NetworkError())).toBe(true);
    expect(shouldRetryQuery(1, apiError(503))).toBe(true);
    expect(shouldRetryQuery(2, apiError(503))).toBe(false);
  });

  it.each([400, 401, 403, 404, 409, 422, 429])("never retries %i", (status) => {
    expect(shouldRetryQuery(0, apiError(status))).toBe(false);
  });
});

describe("roleAtLeast", () => {
  it("orders roles like the backend", () => {
    expect(roleAtLeast("OWNER", "ADMIN")).toBe(true);
    expect(roleAtLeast("ADMIN", "ADMIN")).toBe(true);
    expect(roleAtLeast("MEMBER", "ADMIN")).toBe(false);
    expect(roleAtLeast("VIEWER", "MEMBER")).toBe(false);
  });
});
