"""Timeline categories (Step 7.4, ADR-0042)."""

from typing import get_args

from app.models import WorkflowEventType
from app.workflows.event_categories import CATEGORIES, EventCategory, event_types


def test_every_event_type_has_one_category() -> None:
    assert set(CATEGORIES) == set(WorkflowEventType)
    assert set(CATEGORIES.values()) == set(get_args(EventCategory))


def test_categories_partition_the_event_types() -> None:
    every = [t for category in get_args(EventCategory) for t in event_types(category)]
    assert sorted(every) == sorted(WorkflowEventType)
    assert WorkflowEventType.INTERVENTION_RESOLVED in event_types("people")
