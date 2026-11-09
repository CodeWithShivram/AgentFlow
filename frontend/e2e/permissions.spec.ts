import { expect, test } from "@playwright/test";

import { acceptInvitation, createWorkflow, invite, newUser, visibleText, workspaceUrl } from "./support/helpers";

test("journey 11a: a viewer can read but not change anything", async ({ browser }) => {
  const owner = await newUser(browser, "perm-owner", "Read Only Co");
  const viewer = await newUser(browser, "perm-viewer");
  const workflowPath = await createWorkflow(owner.page, owner.workspaceId, "Audit the onboarding flow");

  const code = await invite(owner.page, owner.workspaceId, viewer.email, "Viewer");
  await acceptInvitation(viewer.page, code, "Read Only Co");
  const { page } = viewer;

  await page.goto(workspaceUrl(owner.workspaceId, "workflows"));
  await expect(page.getByRole("link", { name: "Audit the onboarding flow" })).toBeVisible();
  await expect(page.getByRole("link", { name: "New workflow" })).toHaveCount(0);

  await page.goto(workflowPath);
  await expect(visibleText(page, "You have read-only access to this workflow.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Start planning" })).toHaveCount(0);

  await page.goto(workspaceUrl(owner.workspaceId, "agents/new"));
  await expect(visibleText(page, "Admins create agents")).toBeVisible();
  await page.goto(workspaceUrl(owner.workspaceId, "members"));
  await expect(page.getByRole("button", { name: "Invite" })).toHaveCount(0);

  // The API, not the UI, is the authority: a forged request is refused with 403.
  const csrf = (await viewer.context.cookies()).find((c) => c.name === "agentflow_csrf")?.value ?? "";
  const forged = await page.request.post(
    `http://localhost:8011/api/v1/workspaces/${owner.workspaceId}/workflows`,
    {
      headers: { Origin: "http://localhost:3100", "X-CSRF-Token": csrf },
      data: { prompt: "not allowed" },
    },
  );
  expect(forged.status()).toBe(403);

  await owner.context.close();
  await viewer.context.close();
});

test("journey 11b: a stale edit hits a revision conflict and reloads", async ({ browser }) => {
  const user = await newUser(browser, "conflict");
  const workflowPath = await createWorkflow(user.page, user.workspaceId, "Plan the offsite");

  const stale = await user.context.newPage();
  await stale.goto(workflowPath);
  await expect(stale.getByRole("button", { name: "Start planning" })).toBeVisible();

  // The first tab changes the workflow; the second still holds the old revision.
  await user.page.getByRole("button", { name: "Start planning" }).click();
  await expect(visibleText(user.page, "Planning")).toBeVisible();

  await stale.getByRole("button", { name: "Start planning" }).click();
  const conflict = stale.getByRole("alertdialog", { name: "This workflow changed" });
  await expect(conflict).toBeVisible();
  await conflict.getByRole("button", { name: "Reload latest" }).click();
  await expect(conflict).toBeHidden();
  await expect(visibleText(stale, "Planning")).toBeVisible();
  await expect(stale.getByRole("button", { name: "Submit plan for approval" })).toBeVisible();
  await user.context.close();
});
