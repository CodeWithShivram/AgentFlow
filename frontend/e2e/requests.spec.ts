import { expect, test } from "@playwright/test";

import { createWorkflow, newUser, visibleText, workspaceUrl } from "./support/helpers";

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
  await user.context.close();
});
