# ADR-0057: Web UI projects (HTML/CSS/JS) with browser checks and a page preview

- Status: Accepted
- Date: 2026-11-27
- Implements: Step 9.10; the owner asked for projects with "a basic UI/UX using html/css/js" and chose the full scope
- Amends: ADR-0048 (profiles), ADR-0050 (sandbox images), ADR-0051 (integration checks), ADR-0052 (packages)

## Context
- **Projects are command-line only today.** Both profiles (`python-3.12`, `node-24-typescript`) produce libraries and CLIs. A request such as "a small shop website" has nowhere to put a page, and nothing checks that the page works.
- **Static checks alone miss the usual web bugs.** A page can parse cleanly and still throw in the browser, request a missing file, or leave form fields unlabelled.
- **Constraints stay the same.** Generated code runs only in the offline sandbox, agents never execute anything, and dependencies are curated per profile.

## Decision
### Profile `python-3.12-web`
- **Layout.** A FastAPI backend in `src/<package>/main.py` serves the JSON API under `/api/` and mounts `static/` at `/` (`StaticFiles(..., html=True)`). The UI is plain HTML, CSS and JavaScript in `static/`, with `static/index.html` as the entry. It uses no frameworks, build step or external URLs (CDNs, fonts, analytics).
- **Image** `agentflow-sandbox/python-3.12-web:1`. It is the `python-3.12` profile's pins (FastAPI, uvicorn, httpx, pytest, ...) plus Playwright 1.65.2 with its Chromium, html5lib, and Node.js from Debian (only for `node --check`). It is built locally like the others and never pulled at run time.
- **Checks**, in order:
  1. `compile`
  2. `tests` (pytest; the API is tested with `TestClient`)
  3. `web`: `web_static_check.py` requires `static/index.html` with a doctype, reports html5lib parse errors, refuses external `http(s)://` and `//` URLs in HTML/CSS/JS, and runs `node --check` on every `.js` file. Failures name `static/...` paths, so integration rework (ADR-0056) reaches the owner.
  4. `ui`: `ui_smoke.py` starts the app with uvicorn on 127.0.0.1 inside the container, opens it in headless Chromium (1280×800), and fails on:
     - console errors and uncaught page errors;
     - failed requests and HTTP ≥ 400 responses (named as `static/...` where possible);
     - a missing `<title>` or `lang`;
     - form controls without a label.

     It always takes a full-page screenshot to `.agentflow/ui-preview.png`.
- **Sandbox rules are unchanged:** no network (loopback only), read-only root, non-root, limits and timeout. Chromium runs with `--no-sandbox`, because the container is the isolation boundary (it has no capabilities for Chromium's own sandbox).

### Frontend Developer agent
- **A new built-in, "Frontend Developer"** (`builtin://frontend/v1`, capability `ui_development`). It is a Developer runtime with UI/UX instructions:
  - semantic, accessible HTML;
  - CSS custom properties, a responsive layout;
  - loading, empty and error states;
  - `fetch` with relative `/api/...` URLs;
  - `textContent` rather than `innerHTML` for data.
- **Planning.** Profiles may add `extra_task_skills`; the web profile adds `ui_development`, so the planner can give `static/` to the Frontend Developer. The plan schema accepts it only on that profile. The prompt is `software_plan/v6`.

### Preview
- **Collecting the screenshot.** `DockerSandbox.run(..., collect=[...])` returns named regular files (≤ 5 MB) from the throwaway copy. Profiles name one check and path as `preview`.
- **Storing it.** The integration job stores the screenshot as an artifact of the new kind `UI_PREVIEW` (migration 0036, `image/png`) and links it from the check (`preview_artifact_id`). Storage errors drop only the picture; the run's result stands. The artifact is stored before the run's fields change, because storing flushes.
- **Showing it.** The project page shows the latest preview with its commit, and whether the UI checks passed. A screenshot of a failing page is kept too, since it helps people see what went wrong.
- **Packaging it.** A VERIFIED package includes the passing run's screenshot as `AGENTFLOW_UI_PREVIEW.png` (verified against its size and SHA-256, outside the manifest like the report), and the build report names it. A project file with that name is excluded as reserved.

## Consequences
- **Website requests work end to end.** A prompt like "a small shop with a product list and a cart" gets a FastAPI API, a static UI owned by the Frontend Developer, and browser-level checks, before it can complete.
- **The image is large** (about 2.2 GB, with Chromium). CI builds it with the others; the web tests skip without it.
- **The checks are smoke-level.** They cover load, errors and basic accessibility, not visual quality or full flows; the screenshot lets people judge the look.
- **Seven built-in agents.** Re-run `uv run python -m app.agents.seed` on existing databases.
