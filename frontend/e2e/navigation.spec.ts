import { expect, test } from "@playwright/test";

import { registerViaApi, uniqueEmail, workspaceUrl } from "./support/helpers";

const SECTIONS = [
  ["Workflows", "Workflows"],
  ["Agents", "Agents"],
  ["Agent proposals", "Agent proposals"],
  ["Activity", "Activity"],
  ["Members", "Members"],
  ["Settings", "Workspace settings"],
  ["Dashboard", "Dashboard"],
] as const;

test("journey 3: sidebar navigation, breadcrumbs and theme", async ({ page }) => {
  const workspaceId = await registerViaApi(page, uniqueEmail("nav"), "Nav Workspace");
  await page.goto(workspaceUrl(workspaceId));
  const nav = page.getByRole("navigation", { name: "Main navigation" }).or(page.locator("[data-sidebar=sidebar]"));

  for (const [link, heading] of SECTIONS) {
    await nav.getByRole("link", { name: link, exact: true }).click();
    await expect(page.getByRole("heading", { name: heading, level: 1 })).toBeVisible();
    await expect(nav.getByRole("link", { name: link, exact: true })).toHaveAttribute("aria-current", "page");
  }
  await expect(page.getByRole("navigation", { name: "breadcrumb" })).toContainText("Nav Workspace");

  await page.getByRole("button", { name: "Change theme" }).click();
  await page.getByRole("menuitemradio", { name: "Dark" }).click();
  await expect(page.locator("html")).toHaveClass(/dark/);

  // Keyboard users can skip straight to the content: it is the first stop after a page load.
  await page.reload();
  await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("link", { name: "Skip to main content" })).toBeFocused();
});

test("journey 3 (mobile): navigation opens as a sheet and closes after choosing a page", async ({ browser }) => {
  const context = await browser.newContext({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });
  const page = await context.newPage();
  const workspaceId = await registerViaApi(page, uniqueEmail("mobile"));
  await page.goto(workspaceUrl(workspaceId));
  await expect(page.getByRole("heading", { name: "Dashboard", level: 1 })).toBeVisible();

  // No horizontal page scrolling at phone width on the main pages.
  for (const section of ["dashboard", "workflows", "workflows/new", "agents", "agents/new", "proposals", "members", "settings"]) {
    await page.goto(workspaceUrl(workspaceId, section));
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    expect(overflow, `horizontal overflow on ${section}`).toBeLessThanOrEqual(0);
  }
  await page.goto(workspaceUrl(workspaceId));

  await page.getByRole("button", { name: "Toggle navigation" }).click();
  const sheet = page.getByRole("dialog");
  await sheet.getByRole("link", { name: "Agents", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Agents", level: 1 })).toBeVisible();
  await expect(sheet).toBeHidden();
  await context.close();
});

test("an unknown workspace shows not found instead of data", async ({ page }) => {
  await registerViaApi(page, uniqueEmail("unknown"));
  await page.goto(workspaceUrl("00000000-0000-4000-8000-000000000000"));
  await expect(page.getByText("Not found", { exact: true })).toBeVisible();
});
