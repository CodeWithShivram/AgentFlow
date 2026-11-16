"""Approval audit helpers (Step 6.4, ADR-0038): the action fingerprint and per-action roles."""

import uuid
from typing import Any

from app.execution.runner import approval_role
from app.models import HumanIntervention, WorkspaceRole
from app.workflows.engine import action_fingerprint


def _request(**changes: Any) -> HumanIntervention:
    values: dict[str, Any] = {
        "id": uuid.UUID(int=1),
        "workflow_id": uuid.UUID(int=2),
        "task_id": uuid.UUID(int=3),
        "subject": "agent_action",
        "requested_action": "Email the customer list",
        "context": {"reason": "Outreach.", "risks": ["Twice."], "attempt_number": 1},
    }
    return HumanIntervention(**{**values, **changes})


def test_the_fingerprint_is_stable_and_covers_what_was_shown() -> None:
    fingerprint = action_fingerprint(_request())
    assert len(fingerprint) == 64
    assert fingerprint == action_fingerprint(_request())
    # Key order does not matter; content does.
    reordered = {"attempt_number": 1, "risks": ["Twice."], "reason": "Outreach."}
    assert action_fingerprint(_request(context=reordered)) == fingerprint
    for change in (
        {"requested_action": "Email every customer"},
        {"context": {"reason": "Outreach.", "risks": [], "attempt_number": 1}},
        {"task_id": uuid.UUID(int=4)},
        {"id": uuid.UUID(int=5)},
    ):
        assert action_fingerprint(_request(**change)) != fingerprint


def test_agents_with_high_risk_tools_need_an_owner_to_approve() -> None:
    base = {"system_instruction_ref": "builtin://general/v1", "capabilities": ["report_writing"]}
    assert approval_role(base) == WorkspaceRole.ADMIN
    assert approval_role({**base, "tools": [{"tool": "web_search"}]}) == WorkspaceRole.ADMIN
    assert approval_role({**base, "tools": [{"tool": "test_runner"}]}) == WorkspaceRole.OWNER
