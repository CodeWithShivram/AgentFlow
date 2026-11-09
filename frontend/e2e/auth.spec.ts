import { expect, test } from "@playwright/test";

import { PASSWORD, registerViaApi, registerViaUi, uniqueEmail, visibleText, workspaceUrl } from "./support/helpers";

test("journey 1: register creates an account and a personal workspace", async ({ page, context }) => {
  const login = await page.request.get("/login");
  expect(login.headers()["x-frame-options"]).toBe("DENY");
  expect(login.headers()["x-content-type-options"]).toBe("nosniff");
  expect(login.headers()["x-powered-by"]).toBeUndefined();

  const email = uniqueEmail("register");
  await registerViaUi(page, email, "Acme Research");

  await expect(page).toHaveURL(/\/w\/[0-9a-f-]+\/dashboard$/);
  await expect(visibleText(page, "Overview of Acme Research.")).toBeVisible();

  // Tokens are HttpOnly cookies; only the CSRF token is readable by scripts (ADR-0007).
  const cookies = await context.cookies();
  const access = cookies.find((c) => c.name === "agentflow_access");
  const refresh = cookies.find((c) => c.name === "agentflow_refresh");
  const csrf = cookies.find((c) => c.name === "agentflow_csrf");
  expect(access?.httpOnly).toBe(true);
  expect(refresh?.httpOnly).toBe(true);
  expect(csrf?.httpOnly).toBe(false);
  const storage = await page.evaluate(() => JSON.stringify({ ...localStorage }) + JSON.stringify({ ...sessionStorage }));
  expect(storage).not.toContain(access?.value ?? "missing");
  expect(storage).not.toContain(refresh?.value ?? "missing");
});

test("register shows API validation errors without losing the form", async ({ page }) => {
  const email = uniqueEmail("dupe");
  await registerViaApi(page, email);
  await page.context().clearCookies();
  await page.goto("/register");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByLabel("Confirm password").fill(PASSWORD);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(visibleText(page, /already exists/i)).toBeVisible();
  await expect(page.getByLabel("Email")).toHaveValue(email);
});

test("journey 2: login, session restore on reload, and logout", async ({ page }) => {
  const email = uniqueEmail("login");
  const workspaceId = await registerViaApi(page, email);
  await page.context().clearCookies();

  // A protected page sends an anonymous visitor to sign in and back afterwards.
  await page.goto(workspaceUrl(workspaceId, "agents"));
  await expect(page).toHaveURL(/\/login\?next=/);
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill("wrong-password-123");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(visibleText(page, "Invalid email or password.")).toBeVisible();

  await page.getByLabel("Password").fill(PASSWORD);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("heading", { name: "Agents", level: 1 })).toBeVisible();
  await expect(page).toHaveURL(new RegExp(`${workspaceUrl(workspaceId, "agents")}$`));

  // Session restore: a full reload keeps the user signed in (cookies + /auth/me).
  await page.reload();
  await expect(page.getByRole("heading", { name: "Agents", level: 1 })).toBeVisible();

  await page.getByRole("button", { name: "Account menu" }).click();
  await page.getByRole("menuitem", { name: "Sign out" }).click();
  await expect(page).toHaveURL(/\/login/);
  await page.goto(workspaceUrl(workspaceId));
  await expect(page).toHaveURL(/\/login/);
});

test("journey 10: an expired access token is refreshed silently; a dead session returns to sign in", async ({
  page,
  context,
}) => {
  const workspaceId = await registerViaApi(page, uniqueEmail("expiry"));
  await page.goto(workspaceUrl(workspaceId));
  await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();

  // Simulate access-token expiry: drop only the access cookie. The next call gets 401, the client
  // refreshes once (single flight) and replays the request.
  const refreshes: string[] = [];
  page.on("request", (r) => {
    if (r.url().endsWith("/api/v1/auth/refresh")) refreshes.push(r.headers()["x-csrf-token"] ?? "");
  });
  await context.clearCookies({ name: "agentflow_access" });
  await page.getByRole("link", { name: "Workflows", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Workflows", level: 1 })).toBeVisible();
  await expect(visibleText(page, "No workflows yet")).toBeVisible();
  expect(refreshes).toHaveLength(1);
  expect(refreshes[0]).not.toBe("");

  // Simulate a dead session: no cookies at all. The app must send the user to sign in.
  await context.clearCookies();
  await page.getByRole("link", { name: "Agents", exact: true }).click();
  await expect(page).toHaveURL(/\/login\?.*expired=1/);
  await expect(visibleText(page, "Your session expired")).toBeVisible();
});
