"""Deterministic verification: layer A of handoff section 8 (ADR-0030).

Pure functions over platform data: the task's contract fields, the attempt's stored submission
and the agent version's configuration. No database access, no model call and no agent code. The
rules are versioned (`VERIFIER`); changing a check means a new version. Issues carry a code, a
severity and the criterion, deliverable or schema location they concern, never agent text, so
they are safe to store and show.

Checks, in order:
- submission: the stored submission still parses as an AgentSubmission.
- criteria_coverage: exactly one claim per acceptance criterion, and none for unknown criteria.
- criteria_claims: a criterion the agent itself says is NOT_MET fails; PARTIAL is a warning for
  semantic evaluation (Step 5.2). MET claims stay unverified, because this layer cannot judge
  content.
- deliverables: every deliverable has non-empty content in `outputs`.
- output_schema: `outputs` conforms to the task's expected JSON Schema, when it has one.
- runtime_outputs: Developer and QA outputs fit their stricter models, and QA test cases cover
  only known criteria (ADR-0022).
- platform_note: a runtime's platform note is present unless its version grants a tool that makes
  it untrue (ADR-0022).

A check is skipped when it does not apply, or when the submission could not be read at all.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from jsonschema import Draft202012Validator, SchemaError, validators
from pydantic import ValidationError

from app.agents.contract import AgentSubmission, CriterionStatus
from app.agents.outputs import DeveloperOutputs, QAOutputs, parse_outputs
from app.agents.specialists import RUNTIMES, ModelAgent
from app.projects.scope import in_scope
from app.schemas.agents import AgentConfiguration
from app.tools.registry import REGISTRY, ToolDefinition, usable_grants

# v2 (ADR-0049): adds the `file_scope` check for software project tasks. v3 (ADR-0055 follow-up):
# a project task's files are its deliverables, and extra tests outside its scope only warn.
# v4 (ADR-0056):
# an integration fix that changes nothing is refused (`fix_changes`).
VERIFIER = "deterministic/v4"
# Schema mismatches reported per verification; the rest are summarised by the count.
MAX_SCHEMA_ISSUES = 20


class Severity(StrEnum):
    ERROR = "error"  # fails verification
    WARNING = "warning"  # recorded for semantic evaluation and people; does not fail


class CheckOutcome(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class Issue:
    code: str
    severity: Severity = Severity.ERROR
    criterion_id: str | None = None
    deliverable: str | None = None
    location: str | None = None

    def as_dict(self) -> dict[str, str]:
        values = {
            "code": self.code,
            "severity": self.severity.value,
            "criterion_id": self.criterion_id,
            "deliverable": self.deliverable,
            "location": self.location,
        }
        return {key: value for key, value in values.items() if value is not None}


@dataclass(frozen=True)
class Subject:
    """What one verification looks at; every field is platform data."""

    submission: Mapping[str, Any] | None
    acceptance_criteria: Sequence[Mapping[str, Any]]
    deliverables: Sequence[str]
    output_schema: Mapping[str, Any] | None
    configuration: Mapping[str, Any]
    # Software project tasks only (ADR-0048): the files the task alone may write.
    file_scope: Sequence[str] = ()
    # Integration fix tasks only (ADR-0056): the files the fix replaces, to refuse a no-op fix.
    previous_files: Mapping[str, str] | None = None


@dataclass
class Report:
    checks: list[tuple[str, CheckOutcome]] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(issue.severity == Severity.ERROR for issue in self.issues)

    def error_codes(self) -> list[str]:
        return sorted({i.code for i in self.issues if i.severity == Severity.ERROR})

    def checks_json(self) -> list[dict[str, str]]:
        return [{"name": name, "outcome": outcome.value} for name, outcome in self.checks]

    def issues_json(self) -> list[dict[str, str]]:
        return [issue.as_dict() for issue in self.issues]

    def record(self, name: str, issues: list[Issue] | None) -> None:
        """`None` means the check was skipped."""
        if issues is None:
            self.checks.append((name, CheckOutcome.SKIPPED))
            return
        failed = any(issue.severity == Severity.ERROR for issue in issues)
        self.checks.append((name, CheckOutcome.FAILED if failed else CheckOutcome.PASSED))
        self.issues.extend(issues)


_CHECKS = (
    "submission",
    "criteria_coverage",
    "criteria_claims",
    "deliverables",
    "output_schema",
    "runtime_outputs",
    "platform_note",
    "file_scope",
    "fix_changes",
)


def verify(subject: Subject, registry: Mapping[str, ToolDefinition] = REGISTRY) -> Report:
    report = Report()
    try:
        submission = AgentSubmission.model_validate(subject.submission or {})
    except ValidationError:  # its text may echo agent output
        code = "SUBMISSION_MISSING" if subject.submission is None else "SUBMISSION_INVALID"
        report.record("submission", [Issue(code)])
        for name in _CHECKS[1:]:
            report.record(name, None)
        return report
    report.record("submission", [])

    expected = [str(criterion["id"]) for criterion in subject.acceptance_criteria]
    report.record("criteria_coverage", _coverage(submission, expected))
    report.record("criteria_claims", _claims(submission, expected))
    # v3: a software project task's deliverables are its files (checked by `file_scope`).
    deliverables = None if subject.file_scope else _deliverables(submission, subject.deliverables)
    report.record("deliverables", deliverables)
    report.record("output_schema", _output_schema(submission, subject.output_schema))
    runtime, configuration = _runtime(subject.configuration)
    report.record("runtime_outputs", _runtime_outputs(runtime, submission, expected))
    report.record("platform_note", _platform_note(runtime, configuration, submission, registry))
    report.record("file_scope", _file_scope(submission, subject.file_scope))
    report.record("fix_changes", _fix_changes(submission, subject))
    return report


def _coverage(submission: AgentSubmission, expected: list[str]) -> list[Issue]:
    claimed = [claim.criterion_id for claim in submission.criteria]
    issues = [
        Issue("DUPLICATE_CRITERION_CLAIM", criterion_id=criterion_id)
        for criterion_id in expected
        if claimed.count(criterion_id) > 1
    ]
    issues += [
        Issue("MISSING_CRITERION_CLAIM", criterion_id=criterion_id)
        for criterion_id in expected
        if criterion_id not in claimed
    ]
    # Unknown ids come from the agent, so they are counted, never stored.
    if set(claimed) - set(expected):
        issues.append(Issue("UNKNOWN_CRITERION"))
    return issues


def _claims(submission: AgentSubmission, expected: list[str]) -> list[Issue] | None:
    if not expected:
        return None
    issues = []
    for claim in submission.criteria:
        if claim.criterion_id not in expected:
            continue
        if claim.status == CriterionStatus.NOT_MET:
            issues.append(Issue("CRITERION_NOT_MET", criterion_id=claim.criterion_id))
        elif claim.status == CriterionStatus.PARTIAL:
            issues.append(
                Issue("CRITERION_PARTIAL", Severity.WARNING, criterion_id=claim.criterion_id)
            )
    return issues


def _is_empty(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, list | dict):
        return not value
    return False


def _deliverables(submission: AgentSubmission, deliverables: Sequence[str]) -> list[Issue] | None:
    if not deliverables:
        return None
    return [
        Issue("DELIVERABLE_MISSING", deliverable=name)
        for name in deliverables
        if _is_empty(submission.outputs.get(name))
    ]


def _output_schema(
    submission: AgentSubmission, schema: Mapping[str, Any] | None
) -> list[Issue] | None:
    if schema is None:
        return None
    validator_class = validators.validator_for(schema, default=Draft202012Validator)
    try:
        validator_class.check_schema(schema)
        # Sorted for stable results; only the schema location is kept (task data), never the
        # validator's message, which quotes the agent's output.
        errors = sorted(
            validator_class(schema).iter_errors(submission.outputs),
            key=lambda error: [str(part) for part in error.schema_path],
        )
    except SchemaError:
        return [Issue("OUTPUT_SCHEMA_INVALID")]
    except Exception:  # e.g. an unresolvable $ref; remote references are never fetched
        return [Issue("OUTPUT_SCHEMA_UNUSABLE")]
    issues = [
        Issue(
            "OUTPUT_SCHEMA_MISMATCH",
            location="/".join(str(part) for part in error.schema_path) or "(root)",
        )
        for error in errors[:MAX_SCHEMA_ISSUES]
    ]
    if len(errors) > MAX_SCHEMA_ISSUES:
        issues.append(Issue("OUTPUT_SCHEMA_MORE_MISMATCHES"))
    return issues


def _runtime(
    configuration: Mapping[str, Any],
) -> tuple[type[ModelAgent] | None, AgentConfiguration | None]:
    try:
        parsed = AgentConfiguration.model_validate(configuration)
    except ValidationError:
        return None, None
    runtime = RUNTIMES.get(parsed.system_instruction_ref)
    if runtime is None or not issubclass(runtime, ModelAgent):
        return None, parsed
    return runtime, parsed


def _runtime_outputs(
    runtime: type[ModelAgent] | None, submission: AgentSubmission, expected: list[str]
) -> list[Issue] | None:
    if runtime is None or runtime.outputs_model is None:
        return None
    outputs = parse_outputs(runtime.outputs_model, submission.outputs)
    if outputs is None:
        return [Issue("OUTPUTS_INVALID")]
    if isinstance(outputs, QAOutputs) and not outputs.covered_criteria() <= set(expected):
        return [Issue("TEST_CASE_UNKNOWN_CRITERION")]
    return []


def _platform_note(
    runtime: type[ModelAgent] | None,
    configuration: AgentConfiguration | None,
    submission: AgentSubmission,
    registry: Mapping[str, ToolDefinition],
) -> list[Issue] | None:
    if runtime is None or configuration is None or runtime.platform_note is None:
        return None
    granted = {grant.tool for grant in usable_grants(configuration, registry)}
    if granted & runtime.note_unless_tools:
        return None
    if runtime.platform_note in submission.limitations:
        return []
    return [Issue("PLATFORM_NOTE_MISSING")]


def _file_scope(submission: AgentSubmission, scope: Sequence[str]) -> list[Issue] | None:
    """A software project task must propose files, all inside its scope (ADR-0049). Paths are
    not stored in issues: they are agent text."""
    if not scope:
        return None
    try:
        outputs = DeveloperOutputs.model_validate(submission.outputs or {})
    except ValidationError:
        return [Issue("FILES_MISSING")]
    issues: list[Issue] = []
    outside = sum(1 for f in outputs.proposed_files if not in_scope(scope, f.path))
    if outside:
        issues.append(Issue("FILE_OUTSIDE_SCOPE", location=f"{outside} file(s)"))
    # v3: extra tests outside the scope belong to another task; they are never applied, so
    # they are a warning, not a reason to reject the task's own files.
    extra_tests = sum(1 for f in outputs.proposed_tests if not in_scope(scope, f.path))
    if extra_tests:
        issues.append(
            Issue(
                "TEST_OUTSIDE_SCOPE", severity=Severity.WARNING, location=f"{extra_tests} file(s)"
            )
        )
    return issues


def _fix_changes(submission: AgentSubmission, subject: Subject) -> list[Issue] | None:
    """v4: an integration fix must change the files it replaces; resubmitting them unchanged
    cannot make failing checks pass (ADR-0056). Skipped for other tasks."""
    if subject.previous_files is None:
        return None
    try:
        outputs = DeveloperOutputs.model_validate(submission.outputs or {})
    except ValidationError:
        return None  # reported by the file_scope check
    proposed = {f.path: f.content for f in outputs.proposed_files}
    proposed.update(
        {t.path: t.content for t in outputs.proposed_tests if in_scope(subject.file_scope, t.path)}
    )
    if proposed == dict(subject.previous_files):
        return [Issue("FIX_UNCHANGED")]
    return []
