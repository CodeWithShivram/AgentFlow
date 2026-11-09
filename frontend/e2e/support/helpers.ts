import { type Browser, expect, type Page } from "@playwright/test";

import { API_URL, WEB_URL } from "./stack";

export const PASSWORD = "e2e-password-123456";

let counter = 0;
export function uniqueEmail(label: string): string {
  counter += 1;
  return `${label}-${Date.now().toString(36)}-${counter}@e2e.example.com`;
}

/** Registers through the UI and waits for the dashboard. */
export async function registerViaUi(page: Page, email: string, workspaceName?: string): Promise<void> {
  await page.goto("/register");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password", { exact: true }).fill(PASSWORD);
  await page.getByLabel("Confirm password").fill(PASSWORD);
  if (workspaceName) await page.getByLabel("Workspace name (optional)").fill(workspaceName);
  await page.getByRole("button", { name: "Create account" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();
}

/**
 * Registers through the real API inside the page's browser context, so the session cookies land
 * in the same cookie jar the app uses. Faster than the UI for tests about other features.
 */
export async function registerViaApi(page: Page, email: string, workspaceName?: string): Promise<string> {
  const response = await page.request.post(`${API_URL}/api/v1/auth/register`, {
    headers: { Origin: WEB_URL },
    data: { email, password: PASSWORD, ...(workspaceName ? { workspace_name: workspaceName } : {}) },
  });
  expect(response.status()).toBe(201);
  const body = (await response.json()) as { memberships: { workspace_id: string }[] };
  return body.memberships[0].workspace_id;
}

export async function newUser(browser: Browser, label: string, workspaceName?: string) {
  const context = await browser.newContext();
  const page = await context.newPage();
  const email = uniqueEmail(label);
  const workspaceId = await registerViaApi(page, email, workspaceName);
  return { context, page, email, workspaceId };
}

/** Text that is on screen. Hidden, kept-alive routes (React Activity) can contain the same text. */
export const visibleText = (page: Page, text: string | RegExp) =>
  page.getByText(text, { exact: typeof text === "string" }).filter({ visible: true }).first();

export const workspaceUrl = (workspaceId: string, section = "dashboard") => `/w/${workspaceId}/${section}`;

/** Creates a workflow through the UI and returns its detail URL. */
export async function createWorkflow(
  page: Page,
  workspaceId: string,
  goal: string,
  options: { autonomous?: boolean; budget?: string; currency?: string } = {},
): Promise<string> {
  await page.goto(workspaceUrl(workspaceId, "workflows/new"));
  await page.getByLabel("Goal").fill(goal);
  if (options.autonomous) {
    await page.getByLabel("Plan approval").click();
    await page.getByRole("option", { name: "Autonomous" }).click();
  }
  if (options.budget) await page.getByLabel("Budget limit (optional)").fill(options.budget);
  if (options.currency) await page.getByLabel("Currency").fill(options.currency);
  await page.getByRole("button", { name: "Create workflow" }).click();
  await expect(page.getByRole("heading", { level: 1, name: goal })).toBeVisible();
  return new URL(page.url()).pathname;
}

export async function addTask(page: Page, title: string, dependsOn: string[] = []): Promise<void> {
  await page.getByRole("button", { name: "Add task" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add task" });
  await dialog.getByLabel("Title").fill(title);
  for (const dependency of dependsOn) await dialog.getByRole("checkbox", { name: dependency }).check();
  await dialog.getByRole("button", { name: "Add task" }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole("heading", { level: 3, name: title })).toBeVisible();
}

/** Invites `email` with `role` from the owner's page and returns the one-time code. */
export async function invite(page: Page, workspaceId: string, email: string, role: "Admin" | "Member" | "Viewer") {
  await page.goto(workspaceUrl(workspaceId, "members"));
  await page.getByRole("button", { name: "Invite" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByLabel("Email").fill(email);
  await dialog.getByLabel("Role").click();
  await page.getByRole("option", { name: role }).click();
  await dialog.getByRole("button", { name: "Create invitation" }).click();
  const code = await dialog.getByRole("textbox", { name: "Invitation code" }).inputValue();
  expect(code.length).toBeGreaterThan(10);
  await dialog.getByRole("button", { name: "Done" }).click();
  return code;
}

export async function acceptInvitation(page: Page, code: string, workspaceName: string): Promise<void> {
  await page.goto("/invitations/accept");
  await page.getByLabel("Invitation code").fill(code);
  await page.getByRole("button", { name: "Join workspace" }).click();
  await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();
  await expect(page.getByText(`Overview of ${workspaceName}.`)).toBeVisible();
}
