"""Reading a software project's workspace applications and integration runs (ADR-0049,
ADR-0051). Scoped through the workflow (404 for other workspaces), then by row-level security."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.models import (
    ProjectIntegration,
    ProjectPackage,
    WorkflowKind,
    WorkflowStatus,
    WorkspaceApplication,
)
from app.schemas.projects import (
    IntegrationResponse,
    PackageResponse,
    WorkspaceApplicationResponse,
)
from app.services.authorization import WorkspaceAccess
from app.services.workflows import get_workflow
from app.workflows import engine

LIST_LIMIT = 100


async def list_integrations(
    session: AsyncSession, access: WorkspaceAccess, workflow_id: uuid.UUID
) -> list[IntegrationResponse]:
    workflow = await get_workflow(session, access, workflow_id)
    rows = await session.execute(
        select(ProjectIntegration)
        .where(
            ProjectIntegration.workspace_id == workflow.workspace_id,
            ProjectIntegration.workflow_id == workflow.id,
        )
        .order_by(ProjectIntegration.created_at.desc(), ProjectIntegration.id)
        .limit(LIST_LIMIT)
    )
    return [IntegrationResponse.model_validate(r, from_attributes=True) for r in rows.scalars()]


async def list_applications(
    session: AsyncSession, access: WorkspaceAccess, workflow_id: uuid.UUID
) -> list[WorkspaceApplicationResponse]:
    workflow = await get_workflow(session, access, workflow_id)
    rows = await session.execute(
        select(WorkspaceApplication)
        .where(
            WorkspaceApplication.workspace_id == workflow.workspace_id,
            WorkspaceApplication.workflow_id == workflow.id,
        )
        .order_by(WorkspaceApplication.created_at.desc(), WorkspaceApplication.id)
        .limit(LIST_LIMIT)
    )
    return [
        WorkspaceApplicationResponse.model_validate(r, from_attributes=True) for r in rows.scalars()
    ]


class NotASoftwareProjectError(AppError):
    status_code = 409
    code = "NOT_A_SOFTWARE_PROJECT"
    message = "Only software projects have packages."


class PackagingNotAvailableError(AppError):
    status_code = 409
    code = "PACKAGING_NOT_AVAILABLE"
    message = "A package can be requested once the project's work has stopped or completed."


# A PARTIAL package is an explicit, admin-only exception (ADR-0052): only for projects whose
# work has stopped (blocked, paused) or completed.
_PACKAGEABLE = frozenset({WorkflowStatus.BLOCKED, WorkflowStatus.PAUSED, WorkflowStatus.COMPLETED})


async def list_packages(
    session: AsyncSession, access: WorkspaceAccess, workflow_id: uuid.UUID
) -> list[PackageResponse]:
    workflow = await get_workflow(session, access, workflow_id)
    rows = await session.execute(
        select(ProjectPackage)
        .where(
            ProjectPackage.workspace_id == workflow.workspace_id,
            ProjectPackage.workflow_id == workflow.id,
        )
        .order_by(ProjectPackage.created_at.desc(), ProjectPackage.id)
        .limit(LIST_LIMIT)
    )
    return [PackageResponse.model_validate(r, from_attributes=True) for r in rows.scalars()]


async def request_partial_package(
    session: AsyncSession, access: WorkspaceAccess, workflow_id: uuid.UUID
) -> None:
    """Queues packaging of the workspace's current commit, labelled PARTIAL unless that commit
    passed the integration gate. Packaging itself runs in a worker."""
    workflow = await get_workflow(session, access, workflow_id)
    if workflow.kind != WorkflowKind.SOFTWARE_PROJECT:
        raise NotASoftwareProjectError()
    if workflow.status not in _PACKAGEABLE:
        raise PackagingNotAvailableError(details={"status": workflow.status.value})
    engine.request_package(session, workflow, partial_by=access.user_id)
    await session.commit()
