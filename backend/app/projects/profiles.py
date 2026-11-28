"""Stack profiles (ADR-0048): the curated stacks a software project may use.

A profile fixes the language runtime and the only third-party dependencies a generated project
may declare, because generated code is checked in prebuilt, offline sandbox images (ADR-0046,
Step 9.4) that contain exactly these packages. Its shared files (manifests) must each be owned
by exactly one task, so parallel tasks never edit them concurrently. Changing a profile's
dependencies means a new profile id, never an edit, so confirmed plans stay reproducible.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class StackProfile:
    id: str
    title: str
    language: str
    runtime: str
    # Third-party packages a project may depend on (names only; versions are the image's).
    dependencies: tuple[str, ...]
    # Files every project of this profile has, each owned by exactly one task.
    shared_files: tuple[str, ...]
    # The locally built sandbox image that checks this profile (ADR-0050).
    image: str
    # Layout conventions the planner and agents follow, and the checks rely on.
    conventions: str
    # The integration checks (ADR-0051): (name, fixed command) pairs run in `image`.
    checks: tuple[tuple[str, tuple[str, ...]], ...]
    # Folders tests go in.
    test_paths: tuple[str, ...]
    # Web profiles (ADR-0057): the check that renders the page, and the file it leaves in the
    # project copy (a screenshot) for the platform to keep as a UI preview.
    preview: tuple[str, str] | None = None
    # Capabilities project tasks of this profile may require (besides the Developer's).
    extra_task_skills: tuple[str, ...] = ()
    # Minimal working versions of key files (ADR-0061), given to the tasks whose scope covers
    # them. `<package>` stands for the project's package folder under src/.
    starter_files: tuple[tuple[str, str], ...] = ()


# The image's preinstalled dependencies, linked into the throwaway copy of the project.
_LINK_DEPS = "ln -sfn /opt/deps/node_modules node_modules"

_PYTEST_COMMAND = "PYTHONPATH=src:. python -m pytest -q -p no:cacheprovider --import-mode=importlib"


# Starter files of the web profile (ADR-0061): the structure the web and ui checks need.
_WEB_STARTER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>App name</title>
  <link rel="stylesheet" href="styles.css">
  <script src="app.js" defer></script>
</head>
<body>
  <header><h1>App name</h1></header>
  <main id="app" aria-live="polite"><p>Loading...</p></main>
</body>
</html>
"""
_WEB_STARTER_MAIN = """from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="App name")
# src/<package>/main.py -> the project root, whatever the working directory is.
STATIC_DIR = Path(__file__).resolve().parents[2] / "static"


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# Define every /api route above this line: the static mount catches all other paths.
app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
"""

PROFILES: dict[str, StackProfile] = {
    profile.id: profile
    for profile in (
        StackProfile(
            id="python-3.12",
            title="Python 3.12 (FastAPI, pytest)",
            language="python",
            runtime="Python 3.12",
            dependencies=("fastapi", "pydantic", "httpx", "uvicorn", "sqlalchemy", "pytest"),
            shared_files=("pyproject.toml", "README.md"),
            test_paths=("tests/",),
            conventions=(
                "Put the package in src/<package>/ (with __init__.py) and all tests in "
                "tests/; tests "
                "import the package by name. Checks run with PYTHONPATH=src. No build step."
            ),
            image="agentflow-sandbox/python-3.12:1",
            checks=(
                ("compile", ("python", "-m", "compileall", "-q", ".")),
                (
                    "tests",
                    (
                        "sh",
                        "-c",
                        "PYTHONPATH=src:. python -m pytest -q -p no:cacheprovider"
                        " --import-mode=importlib",
                    ),
                ),
            ),
        ),
        StackProfile(
            id="python-3.12-web",
            title="Python 3.12 web app (FastAPI + HTML/CSS/JS frontend)",
            language="python",
            runtime="Python 3.12",
            dependencies=("fastapi", "pydantic", "httpx", "uvicorn", "sqlalchemy", "pytest"),
            shared_files=("pyproject.toml", "README.md"),
            test_paths=("tests/",),
            conventions=(
                "Backend: src/<package>/ (with __init__.py) and src/<package>/main.py defining "
                "app = FastAPI(); API routes under /api/...; mount the UI last with "
                "app.mount('/', StaticFiles(directory='static', html=True), name='static'). "
                "Frontend: static/index.html, static/styles.css, static/app.js - plain HTML, CSS "
                "and JavaScript, no frameworks, no build step, no external URLs; it calls the API "
                "with fetch on relative URLs. UI tasks own static/ and require ui_development; "
                "backend and tests tasks require code_generation. Tests in tests/ use FastAPI's "
                "TestClient and import the package by name. Checks run with PYTHONPATH=src from "
                "the project root, so directory='static' resolves."
            ),
            image="agentflow-sandbox/python-3.12-web:1",
            checks=(
                ("compile", ("python", "-m", "compileall", "-q", ".")),
                ("tests", ("sh", "-c", _PYTEST_COMMAND)),
                ("web", ("python", "/opt/agentflow/checks/web_static_check.py")),
                ("ui", ("python", "/opt/agentflow/checks/ui_smoke.py")),
            ),
            preview=("ui", ".agentflow/ui-preview.png"),
            extra_task_skills=("ui_development",),
            starter_files=(
                ("static/index.html", _WEB_STARTER_HTML),
                ("src/<package>/main.py", _WEB_STARTER_MAIN),
            ),
        ),
        StackProfile(
            id="node-24-typescript",
            title="Node.js 24 with TypeScript (Express, Vitest)",
            language="typescript",
            runtime="Node.js 24",
            dependencies=(
                "express",
                "zod",
                "typescript",
                "vitest",
                "@types/node",
                "@types/express",
            ),
            shared_files=("package.json", "tsconfig.json", "README.md"),
            test_paths=("tests/",),
            conventions=(
                "Put sources in src/ and tests in tests/ (*.test.ts, importing ../src/...). "
                'package.json uses "type": "module"; tsconfig.json includes src and tests.'
            ),
            image="agentflow-sandbox/node-24-typescript:1",
            checks=(
                ("typecheck", ("sh", "-c", f"{_LINK_DEPS} && tsc -p . --noEmit")),
                ("tests", ("sh", "-c", f"{_LINK_DEPS} && vitest run")),
            ),
        ),
    )
}
