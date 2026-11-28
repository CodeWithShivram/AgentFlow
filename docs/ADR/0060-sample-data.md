# ADR-0060: Generated apps ship with sample data

- Status: Accepted
- Date: 2026-11-28
- Implements: Step 9.14
- Amends: ADR-0057 (the `ui` check), ADR-0048 (planning rules)
- Found by: the owner's shop app showed an empty page. Its products table started empty, so the first run showed nothing until someone added products by hand. The suggested fix was a startup step that inserts about 6 sample products while the table is empty.

## Context
- **Empty apps look broken.** A generated app that stores data (products, books, tasks) works but looks empty on first run, and people cannot judge it.
- **Nothing checked for it.** The planner asked for no sample data, and the UI check passed an empty page.

## Decision
- **Planning rule** (`software_plan/v7`; changes `software_change/v2`). An application that stores or lists data ships with realistic sample data: about 6 to 10 records, created at startup only while the store is empty and never touching records people add later. The task owning the data store gets an acceptance criterion for it, so the Developer must claim it and semantic evaluation checks it. A change to an app that starts empty adds startup sample data.
- **A deterministic check for web projects.** The `ui` check in `python-3.12-web` records the page's successful `GET /api/...` JSON responses during the first load.
  - **What counts as data:** a list, or an object containing lists. `{"status": "ok"}` is not data.
  - **When it fails:** when the page loaded data and every response was empty (`[]`, or `{"items": [], "total": 0}`). An empty cart next to seeded products passes.
  - **The message names `src/<package>/main.py`:** "the page shows no data: every API it loaded returned an empty list (…). Create sample records at startup while the store is empty, so the first run shows content." Integration rework (ADR-0056) then asks that file's owner to add the seeding automatically.
- **No new dependencies or images.** The check script inside the existing image is rebuilt by `scripts/sandbox.ps1 build` (CI builds images fresh).

## Consequences
- **First runs show content,** and the screenshot on the project page shows a populated page.
- **Apps that legitimately start empty fail the check:** a page whose only data APIs return nothing. The owner wanted sample data by default, so this is intended; the fix round adds seed data.
- **Pages that load no data through `/api/` are unaffected,** for example static pages or apps that embed their data.
