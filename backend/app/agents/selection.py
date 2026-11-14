"""Rule-based agent selection (handoff §7, ADR-0023). Pure functions, no database.

Explainable on purpose: every candidate carries one factor per rule with its outcome, points and
a plain reason, so a person can see why an agent was suggested or excluded. The rules are
versioned (`RULES_VERSION`); changing weights or factors means a new version, so stored
rationales stay interpretable.

Hard filters exclude a candidate: missing required skills, or no runtime for its instructions.
Scored: skill fit (specialists before generalists), usable tools and current workload. Not
scored, and said so: execution history (until platform verification, Phase 5), cost (until
pricing, Phase 7), latency (not measured) and workspace policy (none exists).
"""

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from app.agents.instructions import INSTRUCTIONS
from app.schemas.agents import AgentConfiguration
from app.tools.registry import REGISTRY, ToolDefinition, usable_grants

RULES_VERSION = "selection/v1"
# Instruction references with a runtime (the keys of app.agents.specialists.RUNTIMES, asserted
# equal by tests). Imported from instructions so the API never loads the model gateway.
RUNNABLE_REFS = frozenset(INSTRUCTIONS)
SKILL_FIT_POINTS = 40.0
TOOL_FIT_POINTS = 20.0
WORKLOAD_POINTS = 30.0

Outcome = Literal["pass", "fail", "scored", "not_scored"]


@dataclass(frozen=True)
class CandidateInput:
    agent_id: uuid.UUID
    agent_name: str
    agent_version_id: uuid.UUID
    agent_version: int
    configuration: AgentConfiguration
    # Tasks assigned to this agent in the workspace that are ASSIGNED, QUEUED or IN_PROGRESS.
    in_flight: int


@dataclass(frozen=True)
class Factor:
    name: str
    outcome: Outcome
    points: float
    detail: str


@dataclass(frozen=True)
class Candidate:
    agent_id: uuid.UUID
    agent_name: str
    agent_version_id: uuid.UUID
    agent_version: int
    eligible: bool
    score: float
    # 1 = best eligible candidate; None for excluded candidates.
    rank: int | None
    factors: tuple[Factor, ...]


_NOT_SCORED = (
    Factor(
        "history",
        "not_scored",
        0.0,
        "Not scored until platform verification exists: a successful attempt today only means "
        "its output was well-formed.",
    ),
    Factor("cost", "not_scored", 0.0, "Not scored: model pricing arrives in Phase 7."),
    Factor("latency", "not_scored", 0.0, "Not scored: latency is not measured yet."),
    Factor("policy", "not_scored", 0.0, "No workspace selection policy is configured."),
)


def _evaluate(
    required: frozenset[str],
    candidate: CandidateInput,
    registry: Mapping[str, ToolDefinition],
    runnable_refs: frozenset[str],
) -> tuple[bool, list[Factor]]:
    configuration = candidate.configuration
    capabilities = frozenset(configuration.capabilities)
    factors: list[Factor] = []

    missing = sorted(required - capabilities)
    factors.append(
        Factor("skills", "fail", 0.0, f"Missing required skills: {', '.join(missing)}.")
        if missing
        else Factor("skills", "pass", 0.0, "Has every required skill.")
    )
    runnable = configuration.system_instruction_ref in runnable_refs
    factors.append(
        Factor("runtime", "pass", 0.0, "A runtime exists for its instructions.")
        if runnable
        else Factor("runtime", "fail", 0.0, "No runtime exists for its instructions yet.")
    )

    if required:
        share = len(required) / len(capabilities)
        detail = f"Required skills are {len(required)} of its {len(capabilities)} skills."
    else:
        share = 0.0
        detail = "The task lists no required skills."
    factors.append(Factor("skill_fit", "scored", round(SKILL_FIT_POINTS * share, 1), detail))

    granted = configuration.tool_names()
    usable = usable_grants(configuration, registry)
    if granted:
        tool_points = round(TOOL_FIT_POINTS * len(usable) / len(granted), 1)
        tool_detail = f"{len(usable)} of its {len(granted)} granted tools are usable now."
    else:
        tool_points, tool_detail = 0.0, "It is granted no tools."
    factors.append(Factor("tool_fit", "scored", tool_points, tool_detail))

    busy = candidate.in_flight
    factors.append(
        Factor(
            "workload",
            "scored",
            round(WORKLOAD_POINTS / (1 + busy), 1),
            f"{busy} task{'s' if busy != 1 else ''} currently assigned or running.",
        )
    )
    factors.extend(_NOT_SCORED)
    return not missing and runnable, factors


def rank_candidates(
    required_skills: list[str],
    candidates: list[CandidateInput],
    *,
    registry: Mapping[str, ToolDefinition] = REGISTRY,
    runnable_refs: frozenset[str] = RUNNABLE_REFS,
) -> list[Candidate]:
    """Eligible candidates best first (score, then name, then id), then excluded ones."""
    required = frozenset(required_skills)
    evaluated: list[Candidate] = []
    for candidate in candidates:
        is_eligible, factors = _evaluate(required, candidate, registry, runnable_refs)
        evaluated.append(
            Candidate(
                agent_id=candidate.agent_id,
                agent_name=candidate.agent_name,
                agent_version_id=candidate.agent_version_id,
                agent_version=candidate.agent_version,
                eligible=is_eligible,
                score=round(sum(f.points for f in factors), 1) if is_eligible else 0.0,
                rank=None,
                factors=tuple(factors),
            )
        )
    eligible = sorted(
        (c for c in evaluated if c.eligible),
        key=lambda c: (-c.score, c.agent_name.lower(), str(c.agent_id)),
    )
    excluded = sorted(
        (c for c in evaluated if not c.eligible),
        key=lambda c: (c.agent_name.lower(), str(c.agent_id)),
    )
    ranked = [
        Candidate(**{**c.__dict__, "rank": index}) for index, c in enumerate(eligible, start=1)
    ]
    return [*ranked, *excluded]
