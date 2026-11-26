"""Provider schemas (ADR-0055): value bounds are removed, structure and names are kept, and the
gateway still enforces every bound through the Pydantic model."""

import json
from typing import Annotated, Any

from pydantic import BaseModel, Field, StringConstraints

from app.agents.contract import AgentSubmission
from app.llm.litellm_adapter import provider_schema
from app.planning.analysis import PromptAnalysis
from app.planning.schema import PlanProposal

BOUNDS = {"maxLength", "maxItems", "maximum", "minimum", "pattern"}
# Lower size bounds are kept on purpose: they tell the model an answer may not be empty.
KEPT = {"minItems", "minLength"}


class Inner(BaseModel):
    pattern: Annotated[str, StringConstraints(pattern=r"^[a-z]+$", max_length=5)]
    title: str


class Outer(BaseModel):
    format: Annotated[int, Field(ge=1, le=9)]
    items: Annotated[list[Inner], Field(min_length=1, max_length=3)]


def _keys(node: Any, inside_names: bool = False) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if not inside_names:
                found.add(key)
            names = key in {"properties", "$defs"} and not inside_names
            found |= _keys(value, names)
    elif isinstance(node, list):
        for value in node:
            found |= _keys(value)
    return found


def test_bounds_are_removed_and_names_kept() -> None:
    schema = provider_schema(Outer)
    assert not (_keys(schema) & BOUNDS) and "title" not in _keys(schema)
    assert set(schema["properties"]) == {"format", "items"}  # field names that look like keywords
    assert set(schema["$defs"]["Inner"]["properties"]) == {"pattern", "title"}
    assert schema["required"] == ["format", "items"]
    assert schema["properties"]["items"]["items"] == {"$ref": "#/$defs/Inner"}
    assert schema["properties"]["items"]["minItems"] == 1 and KEPT & _keys(schema)


def test_platform_schemas_relax_without_losing_fields() -> None:
    for model in (PromptAnalysis, PlanProposal, AgentSubmission):
        full, relaxed = model.model_json_schema(), provider_schema(model)
        assert not (_keys(relaxed) & BOUNDS)
        assert set(relaxed["properties"]) == set(full["properties"])
        assert relaxed.get("required") == full.get("required")
        assert len(json.dumps(relaxed)) < len(json.dumps(full))
