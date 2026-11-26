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
    # Folders tests go in.
    test_paths: tuple[str, ...]


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
            image="agentflow-sandbox/python-3.12:1",
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
            image="agentflow-sandbox/node-24-typescript:1",
        ),
    )
}
