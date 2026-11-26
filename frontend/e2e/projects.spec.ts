import { expect, test } from "@playwright/test";

import { newUser, visibleText, workspaceUrl } from "./support/helpers";

// Step 9.8 (ADR-0046): the product journey. One prompt becomes a planned, approved software
// project; the platform pre-assigns agents, runs them on the mock model, applies their files to
// the project workspace, runs the integration checks in the Docker sandbox and packages a
// verified ZIP that the member downloads. Needs Docker and `scripts/sandbox.ps1 build`.
test("journey 15: describe software, approve the plan, download the verified project", async ({ browser }) => {
  test.setTimeout(600_000);
  const user = await newUser(browser, "project");
  const { page } = user;

  await page.goto(workspaceUrl(user.workspaceId, "projects/new"));
  await page.getByLabel("What should it build?").fill(
    "Build a small Python command-line tool that counts the words in a text file, with tests.",
  );
  await page.getByRole("button", { name: /Create project/ }).click();
  await expect(page).toHaveURL(/\/projects\/[0-9a-f-]{36}$/);
  const projectUrl = page.url();

  // Planning, approval and start use the existing workflow controls.
  await page.getByRole("link", { name: "Plan & controls" }).first().click();
  await page.getByRole("button", { name: "Start planning" }).click();
  await page.getByRole("tab", { name: /Requirements/ }).click();
  const requirements = page.getByRole("tabpanel", { name: /Requirements/ });
  await expect(requirements.getByText("Draft plan version 1 is ready with 3 tasks")).toBeVisible({
    timeout: 90_000,
  });
  await requirements.getByRole("button", { name: "Review draft" }).click();
  await page.getByRole("button", { name: "Confirm this draft" }).click();
  const confirmDialog = page.getByRole("dialog", { name: "Confirm draft version 1" });
  await confirmDialog.getByRole("button", { name: "Confirm plan" }).click();
  await expect(confirmDialog).toBeHidden();
  // No manual agent assignment: the platform pre-assigned every task (ADR-0054).
  await page.getByRole("button", { name: "Submit plan for approval" }).click();
  await page.getByRole("button", { name: "Approve plan" }).click();
  await page.getByRole("button", { name: "Start workflow" }).click();
  await expect(visibleText(page, "Running")).toBeVisible();

  // The project page follows the work to a verified deliverable.
  await page.goto(projectUrl);
  await expect(page.getByRole("progressbar")).toBeVisible();
  const deliverables = page.getByRole("region", { name: "Deliverables" });
  await expect(deliverables.getByRole("listitem", { name: "Verified package" })).toBeVisible({
    timeout: 480_000,
  });
  await expect(visibleText(page, "Completed")).toBeVisible();
  await expect(page.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "100");
  const integration = page.getByRole("region", { name: "Integration" });
  await expect(integration.getByText(/passed/i).first()).toBeVisible();

  const [download] = await Promise.all([
    page.waitForEvent("download"),
    deliverables.getByRole("link", { name: /Download ZIP/ }).click(),
  ]);
  expect(download.suggestedFilename()).toMatch(/^mock-project-[0-9a-f]{8}\.zip$/);
  const stream = await download.createReadStream();
  const chunks: Buffer[] = [];
  for await (const chunk of stream) chunks.push(chunk as Buffer);
  const bytes = Buffer.concat(chunks);
  expect(bytes.subarray(0, 2).toString()).toBe("PK");
  // The archive lists its report and manifest by name in the central directory.
  expect(bytes.includes(Buffer.from("mock-project/AGENTFLOW_BUILD_REPORT.md"))).toBe(true);
  expect(bytes.includes(Buffer.from("mock-project/AGENTFLOW_MANIFEST.json"))).toBe(true);
  expect(bytes.includes(Buffer.from("mock-project/tests/test_mock.py"))).toBe(true);
  await user.context.close();
});
