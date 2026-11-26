"""Reading a software project's workspace applications and integration runs (ADR-0049,
ADR-0051). Scoped through the workflow (404 for other workspaces), then by row-level security."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProjectIntegration, WorkspaceApplication
from app.schemas.projects import IntegrationResponse, WorkspaceApplicationResponse
from app.services.authorization import WorkspaceAccess
from app.services.workflows import get_workflow

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
