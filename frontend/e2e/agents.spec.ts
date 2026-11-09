import { expect, type Page, test } from "@playwright/test";

import { acceptInvitation, invite, newUser, visibleText, workspaceUrl } from "./support/helpers";

async function fillAgentForm(page: Page, name: string) {
  await page.getByLabel("Name").fill(name);
  await page.getByLabel("Description").fill("Summarises meeting notes.");
  await page.getByLabel("Instruction reference").fill("workspace://summariser/v1");
  await page.getByRole("checkbox", { name: "Report writing" }).check();
  await page.getByRole("checkbox", { name: "Editing" }).check();
}

test("journey 4: create, publish, activate, edit and deactivate an agent", async ({ browser }) => {
  const owner = await newUser(browser, "agents");
  const { page } = owner;
  await page.goto(workspaceUrl(owner.workspaceId, "agents"));
  await expect(page.getByRole("link", { name: /^Writer/ })).toBeVisible(); // built-ins are listed
  await page.getByRole("link", { name: "New agent" }).click();

  // Client-side validation mirrors the API.
  await page.getByRole("button", { name: "Create agent" }).click();
  await expect(visibleText(page, "Choose at least one capability.")).toBeVisible();

  await fillAgentForm(page, "Summariser");
  await page.getByRole("button", { name: "Create agent" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Summariser" })).toBeVisible();
  await expect(visibleText(page, /Not published/)).toBeVisible();

  await page.getByRole("button", { name: "Publish version 1" }).click();
  await expect(visibleText(page, /Active version 1/)).toBeVisible();
  await page.getByRole("button", { name: "Activate" }).click();
  await expect(visibleText(page, "Active")).toBeVisible();

  await page.getByRole("button", { name: "Edit draft" }).click();
  // Only the configuration is versioned (ADR-0008); a description edit alone needs no new version.
  await page.getByLabel("Description").fill("Summarises meeting notes and action items.");
  await page.getByLabel("Timeout (seconds)").fill("300");
  await page.getByRole("button", { name: "Save draft" }).click();
  await expect(page.getByRole("button", { name: "Publish version 2" })).toBeVisible();
  await page.getByRole("button", { name: "Publish version 2" }).click();
  await page.getByRole("tab", { name: /Versions/ }).click();
  await expect(visibleText(page, /Version 2 \(active\)/)).toBeVisible();

  await page.getByRole("button", { name: "Deactivate" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Deactivate" }).click();
  await expect(visibleText(page, "Inactive")).toBeVisible();
  await owner.context.close();
});

test("journey 5: a member proposes an agent and the owner approves it", async ({ browser }) => {
  const owner = await newUser(browser, "proposal-owner", "Proposal Workspace");
  const member = await newUser(browser, "proposal-member");

  const code = await invite(owner.page, owner.workspaceId, member.email, "Member");
  await acceptInvitation(member.page, code, "Proposal Workspace");

  const { page } = member;
  await page.goto(workspaceUrl(owner.workspaceId, "proposals"));
  await page.getByRole("button", { name: "Propose an agent" }).click();
  await fillAgentForm(page, "Minutes Taker");
  await page.getByLabel("Why is this agent needed?").fill("We write minutes for every meeting.");
  await page.getByRole("button", { name: "Submit proposal" }).click();
  await expect(page.getByRole("heading", { name: "Minutes Taker" })).toBeVisible();
  // Members cannot decide proposals.
  await expect(page.getByRole("button", { name: "Approve" })).toHaveCount(0);

  const ownerPage = owner.page;
  await ownerPage.goto(workspaceUrl(owner.workspaceId, "proposals?status=PENDING"));
  await expect(ownerPage.getByRole("heading", { name: "Minutes Taker" })).toBeVisible();
  await ownerPage.getByRole("button", { name: "Approve" }).click();
  await ownerPage.getByLabel("Note (optional)").fill("Looks good.");
  await ownerPage.getByRole("dialog").getByRole("button", { name: "Approve" }).click();
  await expect(ownerPage.getByRole("dialog")).toBeHidden();

  await ownerPage.goto(workspaceUrl(owner.workspaceId, "proposals?status=APPROVED"));
  await ownerPage.getByRole("link", { name: "Open registered agent" }).click();
  await expect(ownerPage.getByRole("heading", { level: 1, name: "Minutes Taker" })).toBeVisible();
  await expect(visibleText(ownerPage, /Active version 1/)).toBeVisible();
  await expect(visibleText(ownerPage, "Inactive")).toBeVisible();

  await owner.context.close();
  await member.context.close();
});
