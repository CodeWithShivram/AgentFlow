"""Workflow request validation: budget_limit (OD-5), currency, settings and task fields."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from app.models import AutonomyPolicy
from app.schemas.workflows import TaskCreateRequest, WorkflowCreateRequest


def _workflow(**settings: Any) -> WorkflowCreateRequest:
    return WorkflowCreateRequest.model_validate({"prompt": "Research X", "settings": settings})


def test_defaults_are_safe() -> None:
    request = WorkflowCreateRequest.model_validate({"prompt": "Research X"})

    assert request.settings.autonomy_policy == AutonomyPolicy.APPROVAL_REQUIRED
    assert request.settings.budget_limit is None
    assert request.settings.currency == "USD"
    assert request.settings.max_parallel_tasks == 3
    assert request.settings.allow_dynamic_agent_proposals is False


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (2, Decimal("2")),
        ("2.00", Decimal("2.00")),
        (0, Decimal("0")),
        ("1000000", Decimal("1000000")),
        ("0.0001", Decimal("0.0001")),
    ],
)
def test_valid_budget_limits(value: Any, expected: Decimal) -> None:
    assert _workflow(budget_limit=value).settings.budget_limit == expected


@pytest.mark.parametrize(
    "value",
    ["-0.01", "1000000.01", "0.00001", "NaN", "Infinity", "-Infinity", "abc", "", True],
)
def test_invalid_budget_limits_are_rejected(value: Any) -> None:
    with pytest.raises(ValidationError):
        _workflow(budget_limit=value)


def test_max_budget_is_not_accepted_as_an_alias() -> None:
    """OD-5: `budget_limit` is the only name."""
    with pytest.raises(ValidationError):
        _workflow(max_budget="2.00")


@pytest.mark.parametrize(("currency", "expected"), [("USD", "USD"), ("eur", "EUR"), ("Gbp", "GBP")])
def test_currency_is_normalized(currency: str, expected: str) -> None:
    assert _workflow(currency=currency).settings.currency == expected


@pytest.mark.parametrize("currency", ["US", "USDT", "U$D", "123", ""])
def test_invalid_currency_is_rejected(currency: str) -> None:
    with pytest.raises(ValidationError):
        _workflow(currency=currency)


@pytest.mark.parametrize(
    "settings",
    [
        {"max_parallel_tasks": 0},
        {"max_parallel_tasks": 101},
        {"autonomy_policy": "yolo"},
        {"deadline": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()},
        {"deadline": "2030-01-01T00:00:00"},  # no timezone
        {"unknown_setting": 1},
    ],
)
def test_invalid_settings_are_rejected(settings: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        _workflow(**settings)


@pytest.mark.parametrize("prompt", ["", "   ", "x" * 20_001])
def test_invalid_prompts_are_rejected(prompt: str) -> None:
    with pytest.raises(ValidationError):
        WorkflowCreateRequest.model_validate({"prompt": prompt})


@pytest.mark.parametrize(
    "task",
    [
        {"title": ""},
        {"title": "t", "required_skills": ["mind_reading"]},
        {"title": "t", "priority": 101},
        {"title": "t", "acceptance_criteria": [{"id": "Bad Id", "description": "d"}]},
        {"title": "t", "expected_output_schema": {"x": "y" * 20_001}},
        {"title": "t", "status": "COMPLETED"},  # status is never client-settable
        {"title": "t", "assigned_agent_id": "00000000-0000-0000-0000-000000000000"},
    ],
)
def test_invalid_tasks_are_rejected(task: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        TaskCreateRequest.model_validate(task)
