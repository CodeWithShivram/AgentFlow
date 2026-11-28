"""Change projects (Step 9.12, ADR-0059): a new software project that changes a completed one.

Its workspace starts as a copy of the base project's files at the commit of the base's latest
passed integration (`base_head_sha`). The plan job seeds it before the first plan, and the apply
job seeds it too if needed (both are idempotent). The planner then sees the current files and
plans only the work the change needs.
"""

from typing import Any

from app.models import Workflow
from app.projects.packaging import MAX_FILES, MAX_TOTAL_BYTES
from app.projects.workspace import ProjectWorkspaces, WorkspaceError

# What the planner sees of the current project (the file list is always complete).
MAX_PLAN_FILES = 60
MAX_PLAN_CHARS = 60_000


def seed_change_workspace(workspaces: ProjectWorkspaces, workflow: Workflow) -> None:
    """Copies the base project's committed files into this project's new repository, once.
    Raises WorkspaceError: BASE_UNAVAILABLE (no base repository), BASE_CHANGED (the base moved
    past the recorded commit) or BASE_TOO_LARGE."""
    if workflow.base_workflow_id is None or workflow.base_head_sha is None:
        return
    project = workspaces.ensure_repository(workflow.workspace_id, workflow.id)
    if workspaces.tracked_files(project):
        return  # already seeded
    base = workspaces.project_path(workflow.workspace_id, workflow.base_workflow_id)
    if not (base / ".git").is_dir():
        raise WorkspaceError("BASE_UNAVAILABLE")
    if workspaces.head(base) != workflow.base_head_sha:
        raise WorkspaceError("BASE_CHANGED")
    tracked = workspaces.tracked_files(base)
    files = workspaces.read_scope(base, None, max_files=MAX_FILES, max_chars=MAX_TOTAL_BYTES)
    if len(files) != len(tracked):
        raise WorkspaceError("BASE_TOO_LARGE")
    workspaces.seed(
        workflow.workspace_id,
        workflow.id,
        files,
        message=f"Start from the verified project at {workflow.base_head_sha[:10]}",
    )


def planner_context(workspaces: ProjectWorkspaces, workflow: Workflow) -> dict[str, Any]:
    """The current project for the planner: its spec, every committed path, and as many file
    contents as fit (data for the model, inside delimiters)."""
    project = workspaces.project_path(workflow.workspace_id, workflow.id)
    paths = sorted(workspaces.tracked_files(project))
    contents = workspaces.read_scope(
        project, None, max_files=MAX_PLAN_FILES, max_chars=MAX_PLAN_CHARS
    )
    spec = workflow.project_spec or {}
    return {
        "name": spec.get("name"),
        "stack_profile": spec.get("stack_profile"),
        "architecture": spec.get("architecture"),
        "ui_flows": spec.get("ui_flows") or [],
        "paths": paths,
        "files": [{"path": path, "content": contents[path]} for path in sorted(contents)],
        "files_left_out": [path for path in paths if path not in contents],
    }
