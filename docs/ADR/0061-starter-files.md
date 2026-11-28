# ADR-0061: Starter files for new web code

- Status: Accepted
- Date: 2026-11-28
- Implements: Step 9.15
- Amends: ADR-0057 (web profile), ADR-0018 (task contract, now v6)

## Context
- **Most first gates of website projects failed the same way.** In 3 of 4 live Gemini web runs, the first integration failed for avoidable structural reasons: `static/index.html` without `<!DOCTYPE html>`, `lang` or `<title>`, or a FastAPI app whose static files were not mounted at `/` (GET / returned 404).
- **Each such failure cost a rework round.** That meant two fix tasks, about 40 s, and extra model calls, before any real feature was checked.

## Decision
- **Profiles may carry starter files.** `StackProfile.starter_files` holds `(path, content)` pairs, where `<package>` stands for the package folder under `src/`. The web profile has two:
  - `static/index.html`: doctype, `lang`, charset, viewport, title, a linked stylesheet and deferred script, header and main;
  - `src/<package>/main.py`: FastAPI with an `/api/health` route, then `app.mount("/", StaticFiles(directory=STATIC_DIR, html=True))` **last**, with `STATIC_DIR` from `Path(__file__).parents[2]`, so it works from any working directory.
- **Which tasks get them** (`app/projects/starters.starters_for`, pure): a task gets the starters its scope covers. The package comes from a scope entry such as `src/shop/` or `src/shop/main.py`; a scope owning all of `src/` keeps the `<package>` placeholder. Fix tasks and change-project tasks get none, because they build on the current files.
- **Contract v6** adds `starter_files` (platform templates, never agent or model text). The prompt adds them with a fixed `starter_rule`: keep the structure (doctype, lang, title, viewport; API routes before the static mount), build the feature on them with real names, and create every file they reference.

## Consequences
- **Fewer wasted rounds.** New web projects start from a structure the `web` and `ui` checks accept, so first gates fail only on real problems.
- **Not enforced.** The checks still decide: a model may ignore the starters, and the rework loop still catches it.
- **Other profiles have no starters yet.** The same mechanism can add them when a recurring failure shows the need.
