"""A workflow's costs and their reconciliation (ADR-0040), for the API.

The figures come from `llm_usage` only (the reporting rule: compose persisted facts, never invent
metrics); `app.llm.costing` does the arithmetic and the checks, on the API's own connection.
"""

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm import costing
from app.schemas.costs import BudgetStatus, CostTotals, WorkflowCostsResponse
from app.services.authorization import WorkspaceAccess
from app.services.workflows import get_workflow


async def workflow_costs(
    session: AsyncSession, access: WorkspaceAccess, workflow_id: uuid.UUID
) -> WorkflowCostsResponse:
    workflow = await get_workflow(session, access, workflow_id)
    costs = await session.run_sync(
        lambda sync: costing.workflow_costs(sync, workflow.workspace_id, workflow.id)
    )
    totals = [
        CostTotals(currency=t.currency, actual=t.actual, estimated=t.estimated, mock=t.mock)
        for t in costs.totals
    ]
    budget = None
    if workflow.budget_limit is not None:
        same = next((t for t in costs.totals if t.currency == workflow.currency), None)
        counted = (same.actual + same.estimated) if same else Decimal(0)
        budget = BudgetStatus(
            limit=workflow.budget_limit,
            currency=workflow.currency,
            comparable=same is not None or not costs.totals,
            counted_spend=counted,
            reached=counted > 0 and counted >= workflow.budget_limit,
        )
    return WorkflowCostsResponse(
        calls=costs.calls,
        calls_by_status=costs.calls_by_status,
        unpriced_calls=costs.unpriced_calls,
        input_tokens=costs.input_tokens,
        output_tokens=costs.output_tokens,
        cached_input_tokens=costs.cached_input_tokens,
        totals=totals,
        budget=budget,
        checked_calls=costs.checked_calls,
        mismatched_calls=costs.mismatched_calls,
        reconciled=costs.mismatched_calls == 0,
    )
