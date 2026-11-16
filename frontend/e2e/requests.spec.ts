import { expect, test } from "@playwright/test";

import { addTask, createWorkflow, newUser, visibleText, workspaceUrl } from "./support/helpers";

test("journey 13: answer the analysis's question from the workflow and find it on the Requests page", async ({
  browser,
}) => {
  const user = await newUser(browser, "requests");
  const { page } = user;
  await page.goto(workspaceUrl(user.workspaceId, "requests"));
  await expect(visibleText(page, "Nothing needs your input")).toBeVisible();

  // A brief request: the worker's analysis (mock provider) asks one question (Step 6.1).
  await createWorkflow(page, user.workspaceId, "Write a report");
  await page.getByRole("button", { name: "Start planning" }).click();
  const needsInput = page.getByRole("region", { name: "Needs your input" });
  await expect(needsInput).toBeVisible({ timeout: 60_000 });
  const question = needsInput.getByRole("article", { name: "Question about the request" });
  await expect(question.getByText(/The request is brief/)).toBeVisible();

  await question.getByLabel("Your answer").fill("Two pages, for executives.");
  await question.getByRole("button", { name: "Send answer" }).click();
  await expect(visibleText(page, "Answer saved. It reaches the next plan draft.")).toBeVisible();
  await expect(needsInput).toBeHidden();

  await page.goto(workspaceUrl(user.workspaceId, "requests?status=RESOLVED"));
  const answered = page.getByRole("article", { name: "Question about the request" });
  await expect(answered.getByText("Two pages, for executives.")).toBeVisible();
  await expect(answered.getByRole("link", { name: "Write a report" })).toBeVisible();

  // Step 6.4: owners and admins reach the approval history from the Requests page.
  await page.getByRole("link", { name: "Approval history" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Approval history" })).toBeVisible();
  await expect(visibleText(page, "No decisions yet")).toBeVisible();
  await user.context.close();
});

test("journey 14: pause a running workflow with a reason, see why it is paused, and resume", async ({
  browser,
}) => {
  const user = await newUser(browser, "pause");
  const { page } = user;
  await createWorkflow(page, user.workspaceId, "Write the quarterly release notes for the team", {
    autonomous: true,
  });
  await page.getByRole("button", { name: "Start planning" }).click();
  await addTask(page, "Draft the notes");
  await page.getByRole("button", { name: "Mark plan ready" }).click();
  await expect(visibleText(page, "Ready")).toBeVisible();
  await page.getByRole("button", { name: "Start workflow" }).click();
  await expect(visibleText(page, "Running")).toBeVisible();

  // Step 6.2: a person's pause records who and, optionally, why.
  await page.getByRole("button", { name: "Pause" }).click();
  const dialog = page.getByRole("alertdialog");
  await dialog.getByLabel("Reason (optional)").fill("Waiting for legal review");
  await dialog.getByRole("button", { name: "Pause" }).click();
  const banner = page.getByRole("region", { name: "Paused" });
  await expect(banner.getByText("Paused by you")).toBeVisible();
  await expect(banner.getByText("Waiting for legal review")).toBeVisible();

  await page.getByRole("button", { name: "Resume" }).click();
  await expect(visibleText(page, "Running")).toBeVisible();
  await expect(banner).toBeHidden();
  await user.context.close();
});
