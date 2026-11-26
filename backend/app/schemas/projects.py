"""Software project API models (ADR-0049, ADR-0051). Check output is untrusted tool text: clients
show it as plain text only."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.models import IntegrationStatus, PackageLabel, PackageStatus, WorkspaceApplicationStatus


class IntegrationResponse(BaseModel):
    id: uuid.UUID
    workflow_id: uuid.UUID
    head_sha: str
    stack_profile: str
    status: IntegrationStatus
    checks: list[dict[str, Any]]
    error_code: str | None
    created_at: datetime
    finished_at: datetime | None


class WorkspaceApplicationResponse(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    execution_id: uuid.UUID
    status: WorkspaceApplicationStatus
    error_code: str | None
    commit_sha: str | None
    files_written: int
    files_removed: int
    created_at: datetime


class PackageResponse(BaseModel):
    """A packaging attempt (ADR-0052). Download READY archives through the artifacts API."""

    id: uuid.UUID
    workflow_id: uuid.UUID
    head_sha: str
    label: PackageLabel
    status: PackageStatus
    error_code: str | None
    archive_artifact_id: uuid.UUID | None
    report_artifact_id: uuid.UUID | None
    file_count: int
    excluded_count: int
    created_at: datetime
