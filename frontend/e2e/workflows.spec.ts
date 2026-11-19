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
  // Step 7.2: spend against the budget, and the cost summary (no model calls yet).
  await expect(visibleText(page, "€0.00 of €125.50")).toBeVisible();
  await expect(page.getByRole("region", { name: "Model costs" }).getByText("No model calls yet.")).toBeVisible();

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
  // Step 8.1: selecting a task opens its chain and attempts; the critical path is on request.
  await stage2.getByRole("button", { name: /^Draft report/ }).click();
  const details = page.getByRole("region", { name: "Task details" });
  await expect(details).toContainText("Waits on directlyCollect sources");
  await expect(details).toContainText("Waited on by (all)Nothing");
  await details.getByRole("button", { name: "Close" }).click();
  await expect(details).toBeHidden();
  await page.getByRole("switch", { name: "Show critical path" }).click();
  await expect(page.getByText("Critical path: Collect sources → Draft report")).toBeVisible();

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
  // Step 3.1: deliverables and criteria are part of the task contract the agent receives.
  await page.getByRole("button", { name: "Add task" }).first().click();
  const taskDialog = page.getByRole("dialog", { name: "Add task" });
  await taskDialog.getByLabel("Title").fill("Draft the notes");
  await taskDialog.getByLabel("Deliverables").fill("Release notes draft");
  await taskDialog.getByLabel("Acceptance criteria").fill("Lists every change");
  await taskDialog.getByRole("button", { name: "Add task" }).click();
  await expect(taskDialog).toBeHidden();
  // Like addTask(): wait until the page shows the new task, so the next action uses the
  // workflow revision after the edit (a stale one is correctly refused as a conflict).
  await expect(page.getByRole("heading", { level: 3, name: "Draft the notes" })).toBeVisible();
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
  // Step 3.6: agents are ranked with reasons; the member may pick any suitable one.
  await expect(dialog.getByText("Suggested", { exact: true })).toBeVisible();
  await dialog.getByRole("radio", { name: /Writer/ }).click();
  await dialog.getByRole("button", { name: "Assign" }).click();
  await expect(dialog).toBeHidden();

  // Journey 9: the outbox relay, Celery worker and Writer runtime (mock model) run the task;
  // Step 5.1: platform verification then accepts it, which completes the one-task workflow.
  await expect(visibleText(page, "Completed")).toBeVisible({ timeout: 60_000 });
  // Step 4.1: the orchestration graph's Next step banner is gone once the workflow is finished.
  await expect(page.getByRole("region", { name: "Next step" })).toBeHidden({ timeout: 30_000 });
  await page.getByRole("button", { name: "Details" }).click();
  const attempts = page.getByRole("list", { name: "Execution attempts for Draft the notes" });
  await expect(attempts.getByText("Attempt 1", { exact: true })).toBeVisible();
  await expect(attempts.getByText("Succeeded", { exact: true })).toBeVisible();
  await expect(attempts.getByText("Writer · version 1")).toBeVisible();
  await expect(page.getByText("Release notes draft", { exact: true })).toBeVisible();
  // The structured submission: the agent's own, unverified claim per criterion.
  const claims = attempts.getByRole("region", { name: "Agent's criterion claims" });
  await expect(claims.getByRole("listitem")).toContainText("MetLists every change");
  await expect(attempts.getByText("Mock model provider: no real model was used.")).toBeVisible();
  const checks = attempts.getByRole("region", { name: "Platform verification: deterministic checks" });
  await expect(checks.getByText("Passed", { exact: true })).toBeVisible();
  await expect(checks.getByText("deterministic/v1")).toBeVisible();
  // Step 5.2: then the semantic evaluation (mock evaluator) judged each criterion.
  const evaluation = attempts.getByRole("region", { name: "Platform verification: semantic evaluation" });
  await expect(evaluation.getByText("Passed", { exact: true })).toBeVisible();
  const findings = evaluation.getByRole("group", { name: "Evaluator findings" });
  await expect(findings.getByRole("listitem")).toContainText("MetLists every change");

  await page.getByRole("tab", { name: "Timeline" }).click();
  const timeline = page.getByRole("list", { name: "Workflow events, newest first" });
  await expect(timeline.getByText("Task completed", { exact: true }).first()).toBeVisible();
  await expect(timeline.getByText("Task started", { exact: true }).first()).toBeVisible();
  await expect(timeline.getByText("Task assigned", { exact: true }).first()).toBeVisible();
  await expect(timeline.getByText("Workflow created", { exact: true })).toBeVisible();

  // Step 7.4: the execution report of the finished workflow, from its records.
  await page.getByRole("link", { name: "Execution report" }).click();
  const report = page.getByRole("article", { name: "Execution report" });
  await expect(report.getByRole("heading", { level: 1, name: "Execution report" })).toBeVisible();
  await expect(report.getByText("Final", { exact: true })).toBeVisible();
  const deliverables = page.getByRole("region", { name: "Deliverables" });
  await expect(deliverables.getByText("Release notes draft", { exact: true })).toBeVisible();
  await expect(page.getByRole("region", { name: "Execution and verification" })).toContainText("Succeeded");
  // Step 8.2: metrics, the dependency graph and the execution timeline complete the report.
  await expect(page.getByRole("region", { name: "Metrics" })).toContainText("Attempts1 (1 succeeded)");
  await expect(page.getByRole("region", { name: "Report stage 1" })).toContainText("Draft the notes");
  const reportTimeline = page.getByRole("list", { name: "Workflow events, oldest first" });
  await expect(reportTimeline.getByText("Task completed", { exact: true }).first()).toBeVisible();

  // Step 8.2: the dashboard summarises usage, and Analytics charts it over time.
  await page.goto(workspaceUrl(user.workspaceId, "dashboard"));
  const usage = page.getByRole("region", { name: "Usage, last 30 days" });
  await expect(usage).toContainText("Agent attempts succeeded100% (1 of 1)");
  await expect(usage).not.toContainText("No model calls yet");
  await usage.getByRole("link", { name: "Analytics" }).click();
  const overTime = page.getByRole("region", { name: "Usage over time" });
  await expect(overTime.getByRole("group", { name: /Tokens per day/ })).toBeVisible();

  await page.goto(workspaceUrl(user.workspaceId, "activity"));
  await expect(visibleText(page, "Task started")).toBeVisible();
  await user.context.close();
});

test("journey 12: pre-assigned tasks run by themselves, the dependent one after both are verified", async ({
  browser,
}) => {
  // Three tasks run and are verified one after another on the E2E stack's single worker, each
  // waiting for the 5-second outbox relay twice (dispatch, then verification).
  test.setTimeout(240_000);
  const user = await newUser(browser, "wf-parallel");
  const { page } = user;
  await createWorkflow(page, user.workspaceId, "Prepare a launch brief");
  await page.getByRole("button", { name: "Start planning" }).click();
  await addTask(page, "Gather facts");
  await addTask(page, "Check figures");
  await addTask(page, "Write the brief", ["Gather facts", "Check figures"]);

  const card = (title: string) =>
    page
      .getByRole("list", { name: "Tasks" })
      .getByRole("listitem")
      .filter({ has: page.getByRole("heading", { level: 3, name: title }) })
      .filter({ visible: true });

  // Step 4.4: tasks still waiting for the workflow to start can be pre-assigned.
  for (const title of ["Gather facts", "Check figures", "Write the brief"]) {
    await page.getByRole("button", { name: `Actions for ${title}` }).click();
    await page.getByRole("menuitem", { name: "Pre-assign agent…" }).click();
    const dialog = page.getByRole("dialog", { name: "Assign an agent" });
    await dialog.getByRole("radio", { name: /Writer/ }).click();
    await dialog.getByRole("button", { name: "Assign" }).click();
    await expect(dialog).toBeHidden();
    await expect(card(title).getByText("Pending", { exact: true })).toBeVisible();
  }

  await page.getByRole("button", { name: "Submit plan for approval" }).click();
  await page.getByRole("button", { name: "Approve plan" }).click();
  await expect(visibleText(page, "Ready")).toBeVisible();
  await page.getByRole("button", { name: "Start workflow" }).click();
  await expect(visibleText(page, "Running")).toBeVisible();

  // Step 4.6: the scheduler dispatches both independent tasks without anyone assigning them.
  // Step 5.1: platform verification completes each one; only then does the task that needs both
  // become ready, run by itself and complete the workflow.
  for (const title of ["Gather facts", "Check figures", "Write the brief"]) {
    await expect(card(title).getByText("Completed", { exact: true })).toBeVisible({ timeout: 120_000 });
  }
  await expect(page.getByRole("region", { name: "Next step" })).toBeHidden({ timeout: 30_000 });
  await user.context.close();
});
