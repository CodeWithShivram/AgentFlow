"""Structured outputs of the Developer and QA runtimes (ADR-0022).

`AgentSubmission.outputs` is a free object; these models are the stricter shapes those two
runtimes must produce inside it. Nothing here is executed or written anywhere: proposed files are
text for a person (and, later, workspace tools) to review, and test cases are designs.
"""

import re
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.schemas.agents import SafeText, Slug

Text = Annotated[SafeText, StringConstraints(min_length=1, max_length=2000, strip_whitespace=True)]
_ALLOW_DELIVERABLES = ConfigDict(extra="allow", frozen=True)
_STRICT = ConfigDict(extra="forbid", frozen=True)

# Folders and files a proposal must never target, even before anything can write them.
_FORBIDDEN_SEGMENTS = frozenset({".git", ".ssh", ".aws", ".azure", ".kube", ".docker", ".gnupg"})
_SEGMENT = re.compile(r"^[A-Za-z0-9._@+-]{1,100}$")
_MAX_SEGMENTS = 12
_MAX_DELIVERABLE_NOTE = 20_000


def _safe_relative_path(value: str) -> str:
    """A relative POSIX path inside a workspace: no traversal, absolute paths, drive letters,
    Windows separators, alternate data streams or secret-holding folders."""
    if value != value.strip() or "\\" in value or value.startswith("/"):
        raise ValueError("path must be relative and use '/' separators")
    segments = value.split("/")
    if len(segments) > _MAX_SEGMENTS:
        raise ValueError(f"path may have at most {_MAX_SEGMENTS} segments")
    for segment in segments:
        if segment in ("", ".", "..") or not _SEGMENT.fullmatch(segment):
            raise ValueError("path segments must be plain names (no '..', '.', ':' or spaces)")
        lowered = segment.lower()
        if lowered in _FORBIDDEN_SEGMENTS:
            raise ValueError("path must not target version-control or credential folders")
        if lowered == ".env" or (lowered.startswith(".env.") and lowered != ".env.example"):
            raise ValueError("path must not target environment files")
    return value


RelativePath = Annotated[str, StringConstraints(min_length=1, max_length=300)]


class ProposedFile(BaseModel):
    model_config = _STRICT

    path: RelativePath
    language: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9+#.-]{0,39}$")]
    purpose: Text
    content: Annotated[SafeText, StringConstraints(max_length=50_000)]

    _path = field_validator("path")(_safe_relative_path)


def _deliverable_notes(model: BaseModel) -> None:
    for key, value in (model.model_extra or {}).items():
        if not isinstance(value, str) or len(value) > _MAX_DELIVERABLE_NOTE:
            raise ValueError(f"deliverable entry {key[:50]!r} must be text")


class DeveloperOutputs(BaseModel):
    """Proposed source and test files, plus one text entry per deliverable (extra keys)."""

    model_config = _ALLOW_DELIVERABLES

    proposed_files: Annotated[list[ProposedFile], Field(min_length=1, max_length=50)]
    proposed_tests: Annotated[list[ProposedFile], Field(max_length=50)] = []

    @model_validator(mode="after")
    def _check(self) -> "DeveloperOutputs":
        paths = [f.path for f in (*self.proposed_files, *self.proposed_tests)]
        if len(set(paths)) != len(paths):
            raise ValueError("each proposed path may appear once")
        _deliverable_notes(self)
        return self


class TestCase(BaseModel):
    model_config = _STRICT

    id: Slug
    title: Text
    # Acceptance criterion ids this case checks; they must exist in the task contract.
    covers: Annotated[list[Slug], Field(max_length=50)]
    kind: Literal["manual", "automated"]
    steps: Annotated[list[Text], Field(min_length=1, max_length=30)]
    expected: Text


class QAOutputs(BaseModel):
    """Designed test cases (never run here), plus one text entry per deliverable."""

    model_config = _ALLOW_DELIVERABLES

    test_cases: Annotated[list[TestCase], Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def _check(self) -> "QAOutputs":
        ids = [case.id for case in self.test_cases]
        if len(set(ids)) != len(ids):
            raise ValueError("test case ids must be unique")
        _deliverable_notes(self)
        return self

    def covered_criteria(self) -> set[str]:
        return {criterion for case in self.test_cases for criterion in case.covers}


def parse_outputs[T: BaseModel](model: type[T], outputs: dict[str, Any]) -> T | None:
    """The outputs as `model`, or None when they do not fit (the reason is never echoed)."""
    try:
        return model.model_validate(outputs)
    except ValueError:
        return None
