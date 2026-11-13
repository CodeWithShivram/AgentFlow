import { expect, test } from "@playwright/test";

import { addTask, createWorkflow, newUser, visibleText, workspaceUrl } from "./support/helpers";

test("journey 6: create a workflow with a budget limit and currency", async ({ browser }) => {
  const user = await newUser(browser, "wf-create");
  const { page } = user;
  await page.goto(workspaceUrl(user.workspaceId, "workflows"));
  await expect(visibleText(page, "No workflows yet")).toBeVisible();

  await page.goto(workspaceUrl(user.workspaceId, "workflows/new"));
  await page.getByLabel("Goal").fill("Compare three CRM vendors");
  await page.getByLabel("Budget limit (optional)").fill("12.34567");
  await page.getByRole("button", { name: "Create workflow" }).click();
  await expect(visibleText(page, "Use a number with up to 4 decimals.")).toBeVisible();

  const goal = "Compare three CRM vendors";
  await createWorkflow(page, user.workspaceId, goal, { budget: "125.50", currency: "eur" });
  await expect(visibleText(page, "Pending")).toBeVisible();
  await page.getByRole("tab", { name: "Settings" }).click();
  await expect(visibleText(page, "€125.50")).toBeVisible();
  await expect(visibleText(page, "Not measured yet (Phase 2)")).toBeVisible();

  await page.goto(workspaceUrl(user.workspaceId, "workflows?status=PENDING"));
  await expect(page.getByRole("link", { name: goal })).toBeVisible();
  await page.goto(workspaceUrl(user.workspaceId, "workflows?status=RUNNING"));
  await expect(visibleText(page, "No running workflows")).toBeVisible();
  await user.context.close();
});

test("journey 7: plan tasks with dependencies", async ({ browser }) => {
  const user = await newUser(browser, "wf-tasks");
  const { page } = user;
  await createWorkflow(page, user.workspaceId, "Write a market report");
  await page.getByRole("button", { name: "Start planning" }).click();
  await expect(visibleText(page, "Planning")).toBeVisible();

  // Step 2.2: a worker analyses the request through the model gateway (mock provider) and the
  // page picks the result up by polling.
  await page.getByRole("tab", { name: /Requirements/ }).click();
  const requirements = page.getByRole("tabpanel", { name: /Requirements/ });
  await expect(requirements.getByText("Objective", { exact: true })).toBeVisible({ timeout: 60_000 });
  await expect(requirements.getByText("Write a market report", { exact: true }).first()).toBeVisible();
  // The request is brief, so the analysis lists an open question.
  await expect(requirements.getByText("Open questions (1)")).toBeVisible();
  // Step 2.3: the ready analysis is turned into a plan draft by the worker; tasks stay unchanged.
  await expect(requirements.getByText("Draft plan version 1 is ready with 1 task")).toBeVisible({
    timeout: 60_000,
  });
  // Step 2.4: the preview shows the draft by stage, validated against the workspace's agents.
  await requirements.getByRole("button", { name: "Review draft" }).click();
  const draftStage = page.getByRole("region", { name: "Draft stage 1" });
  await expect(draftStage.getByText("Write a market report", { exact: true })).toBeVisible();
  await expect(page.getByRole("tabpanel", { name: /Plan draft/ }).getByText("No problems found.")).toBeVisible();
  // Step 2.5: confirming the draft creates its task; the page switches to the Tasks tab.
  await page.getByRole("button", { name: "Confirm this draft" }).click();
  const confirmDialog = page.getByRole("dialog", { name: "Confirm draft version 1" });
  await confirmDialog.getByRole("button", { name: "Confirm plan" }).click();
  await expect(confirmDialog).toBeHidden();
  await expect(page.getByRole("heading", { level: 3, name: "Write a market report" })).toBeVisible();

  await addTask(page, "Collect sources");
  await addTask(page, "Draft report", ["Collect sources"]);
  await addTask(page, "Optional appendix");
  await expect(visibleText(page, "After: Collect sources")).toBeVisible();

  // Mark the appendix optional.
  await page.getByRole("button", { name: "Actions for Optional appendix" }).click();
  await page.getByRole("menuitem", { name: "Edit task" }).click();
  const edit = page.getByRole("dialog", { name: "Edit task" });
  await edit.getByRole("switch").click();
  await edit.getByRole("button", { name: "Save task" }).click();
  await expect(edit).toBeHidden();
  await expect(visibleText(page, "Optional")).toBeVisible();

  await page.getByRole("tab", { name: "Dependencies" }).click();
  const stage2 = page.getByRole("region", { name: "Stage 2" });
  await expect(stage2.getByText("Draft report")).toBeVisible();

  // A cycle is rejected by the API and explained.
  await page.getByRole("tab", { name: /Tasks/ }).click();
  await page.getByRole("button", { name: "Actions for Collect sources" }).click();
  await page.getByRole("menuitem", { name: "Dependencies…" }).click();
  const deps = page.getByRole("dialog", { name: "Dependencies" });
  await deps.getByLabel("Add a dependency").click();
  await page.getByRole("option", { name: "Draft report" }).click();
  await deps.getByRole("button", { name: "Add" }).click();
  await expect(deps.getByRole("alert")).toBeVisible();
  await deps.getByRole("button", { name: "Done" }).click();

  // Remove the dependency; the draft moves back to stage 1.
  await page.getByRole("button", { name: "Actions for Draft report" }).click();
  await page.getByRole("menuitem", { name: "Dependencies…" }).click();
  await deps.getByRole("button", { name: "Remove dependency on Collect sources" }).click();
  await expect(deps.getByText("No dependencies.")).toBeVisible();
  await deps.getByRole("button", { name: "Done" }).click();

  // Remove a task from the plan; it stays as cancelled history.
  await page.getByRole("button", { name: "Actions for Optional appendix" }).click();
  await page.getByRole("menuitem", { name: "Remove from plan" }).click();
  await page.getByRole("alertdialog").getByRole("button", { name: "Remove task" }).click();
  await expect(page.getByRole("button", { name: "Show 1 removed task" })).toBeVisible();
  await user.context.close();
});

test("journeys 8 and 9: approve, start, assign an agent and monitor execution", async ({ browser }) => {
  const user = await newUser(browser, "wf-run");
  const { page } = user;
  await createWorkflow(page, user.workspaceId, "Write release notes");
  await page.getByRole("button", { name: "Start planning" }).click();
  await addTask(page, "Draft the notes");
  await page.getByRole("button", { name: "Submit plan for approval" }).click();
  await expect(visibleText(page, "Awaiting approval")).toBeVisible();
  await page.getByRole("button", { name: "Approve plan" }).click();
  await expect(visibleText(page, "Ready")).toBeVisible();
  await page.getByRole("button", { name: "Start workflow" }).click();
  await expect(visibleText(page, "Running")).toBeVisible();
  await expect(visibleText(page, "Live updates on")).toBeVisible();

  // Journey 8: assign a built-in agent; the task pins its published version.
  await page.getByRole("button", { name: "Assign agent" }).click();
  const dialog = page.getByRole("dialog", { name: "Assign an agent" });
  await dialog.getByRole("radio", { name: /Writer/ }).click();
  await dialog.getByRole("button", { name: "Assign" }).click();
  await expect(dialog).toBeHidden();

  // Journey 9: the outbox relay, Celery worker and mock agent run the task; polling shows it.
  await expect(visibleText(page, "Submitted")).toBeVisible({ timeout: 60_000 });
  await page.getByRole("button", { name: "Details" }).click();
  const attempts = page.getByRole("list", { name: "Execution attempts for Draft the notes" });
  await expect(attempts.getByText("Attempt 1", { exact: true })).toBeVisible();
  await expect(attempts.getByText("Succeeded", { exact: true })).toBeVisible();
  await expect(attempts.getByText("Writer · version 1")).toBeVisible();

  await page.getByRole("tab", { name: "Timeline" }).click();
  const timeline = page.getByRole("list", { name: "Workflow events, newest first" });
  await expect(timeline.getByText("Task started", { exact: true }).first()).toBeVisible();
  await expect(timeline.getByText("Task assigned", { exact: true }).first()).toBeVisible();
  await expect(timeline.getByText("Workflow created", { exact: true })).toBeVisible();

  await page.goto(workspaceUrl(user.workspaceId, "activity"));
  await expect(visibleText(page, "Task started")).toBeVisible();
  await user.context.close();
});
