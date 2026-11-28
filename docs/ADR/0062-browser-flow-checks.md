# ADR-0062: Planned user flows checked in the browser

- Status: Accepted
- Date: 2026-11-28
- Implements: Step 9.16
- Amends: ADR-0048 (project spec), ADR-0050 (sandbox inputs), ADR-0057 (the `ui` check), ADR-0018 (task contract, now v7)

## Context
- **"Verified" did not mean the feature works.** The `ui` check proved the first page loads without errors, but never clicked anything. In the live checkout change (ADR-0059), the new Checkout button only appears once the cart has items. The check, and the screenshot, could not show whether checkout worked at all.
- **The tests miss the browser.** Generated pytest tests cover the API, not the page that calls it.

## Decision
- **The plan names the flows.** For `python-3.12-web` projects, `project.ui_flows` holds up to 3 flows of up to 10 steps each (`UiFlow`, `UiStep` in `app/planning/schema.py`). Each step is one of:
  - `click`: a button or link, by its visible name;
  - `fill`: a field by its label, with a `value`;
  - `expect_text`: a visible text.

  Only `fill` has a value; flows are refused on profiles without a browser check. They are written by the planner (`software_plan/v8`; `software_change/v3` keeps the flows that still apply and adds one for the change), so the checks come from the request, not from the code being checked.
- **The builders see the flows.** Contract v7 gives every task of the project `ui_flows` with a fixed `ui_flows_rule`: use exactly these names, labels and texts.
- **The sandbox runs them.**
  - `DockerSandbox.run(..., inputs=)` writes platform files into the copy's `.agentflow/` folder. That folder is emptied first, so a project can never plant flows or a fake screenshot; only flat names are allowed.
  - The integration job passes the workflow's flows to the preview check as `.agentflow/ui-flows.json`.
  - `ui_smoke.py` first takes the screenshot of the first page, then runs each flow on a fresh page against the same server: Playwright `get_by_role` (button or link) / `get_by_label` / `get_by_text`, 5 s per step. JavaScript errors and HTTP errors during flows are reported too.
  - A failing step is reported as `static/index.html: UI flow '<name>' step N (<action> '<target>'): no visible button or link named '…'` (or "no field labelled", or "the text … did not appear"), so integration rework (ADR-0056) reaches the UI's owner.
- **The mock web plan** includes a flow for the mock page (`MOCK_UI_FLOWS`).

### Amendment (2026-11-28): the element a person would use
- **Found by the recipe-box change run:** correct pages failed, and two fix rounds were wasted.
  - `expect_text 'Ingredients'` waited for the *first* match, a label in a hidden form, instead of the visible detail heading.
  - `fill 'Category'` took the *first* labelled field, the page's filter dropdown, instead of the field in the open add-recipe dialog. `fill` cannot set a dropdown at all.
- **Now:**
  - Steps use only **visible** matches, preferring those inside an open dialog (`dialog[open]`, `[role=dialog]`, `[aria-modal=true]`).
  - Dropdowns are chosen by option text or value. A missing option is reported as such: "the dropdown labelled 'Category' has no option 'Snack' (options: …)", not as a missing field.
  - A sandbox regression test reproduces all three cases.

## Consequences
- **Verified web projects did their main journeys in a real browser,** not only "the page loads".
- **Matching is loose by design.** Name matching is Playwright's default (case-insensitive substring), and flows share one server, so earlier flows may change data later ones see. Planned names that the UI words differently fail the check and go to rework.
- **The flows only cover what was planned.** People can still add flows in a change request.
