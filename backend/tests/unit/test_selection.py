"""Rule-based agent selection (Step 3.6, ADR-0023): pure ranking with explainable factors."""

import uuid
from typing import Any

from app.agents.selection import (
    RULES_VERSION,
    RUNNABLE_REFS,
    CandidateInput,
    rank_candidates,
)
from app.agents.specialists import RUNTIMES
from app.schemas.agents import AgentConfiguration
from tests.unit.test_tool_gateway import TEST_REGISTRY


def _agent(
    name: str,
    capabilities: list[str],
    *,
    ref: str = "builtin://writer/v1",
    in_flight: int = 0,
    tools: list[dict[str, str]] | None = None,
) -> CandidateInput:
    configuration: dict[str, Any] = {
        "system_instruction_ref": ref,
        "capabilities": capabilities,
        "tools": tools or [],
    }
    return CandidateInput(
        agent_id=uuid.uuid5(uuid.NAMESPACE_DNS, name),
        agent_name=name,
        agent_version_id=uuid.uuid4(),
        agent_version=1,
        configuration=AgentConfiguration.model_validate(configuration),
        in_flight=in_flight,
    )


def _factors(candidate: Any) -> dict[str, Any]:
    return {f.name: f for f in candidate.factors}


def test_runnable_references_match_the_runtimes() -> None:
    assert RUNNABLE_REFS == frozenset(RUNTIMES)
    assert RULES_VERSION == "selection/v1"


def test_hard_filters_exclude_with_reasons_and_never_rank() -> None:
    ranked = rank_candidates(
        ["report_writing"],
        [
            _agent("Writer", ["report_writing", "documentation"]),
            _agent("Researcher", ["research"], ref="builtin://researcher/v1"),
            _agent("Custom", ["report_writing"], ref="workspace://prompts/w/v1"),
        ],
    )

    assert [(c.agent_name, c.eligible, c.rank) for c in ranked] == [
        ("Writer", True, 1),
        ("Custom", False, None),
        ("Researcher", False, None),
    ]
    custom, researcher = _factors(ranked[1]), _factors(ranked[2])
    assert custom["runtime"].outcome == "fail" and custom["skills"].outcome == "pass"
    assert researcher["skills"].outcome == "fail"
    assert researcher["skills"].detail == "Missing required skills: report_writing."
    assert ranked[1].score == 0.0 and ranked[2].score == 0.0


def test_specialists_rank_above_generalists_and_workload_counts() -> None:
    ranked = rank_candidates(
        ["report_writing"],
        [
            _agent("Broad", ["report_writing", "documentation", "editing", "general_task"]),
            _agent("Focused", ["report_writing"]),
            _agent("Busy focused", ["report_writing"], in_flight=2),
        ],
    )

    assert [c.agent_name for c in ranked] == ["Focused", "Busy focused", "Broad"]
    focused, busy, broad = (_factors(c) for c in ranked)
    assert focused["skill_fit"].points == 40.0 and broad["skill_fit"].points == 10.0
    assert focused["workload"].points == 30.0 and busy["workload"].points == 10.0
    assert busy["workload"].detail == "2 tasks currently assigned or running."
    assert ranked[0].score == 70.0 and ranked[1].score == 50.0 and ranked[2].score == 40.0


def test_ties_break_by_name_and_tasks_without_skills_still_rank() -> None:
    ranked = rank_candidates([], [_agent("beta", ["editing"]), _agent("Alpha", ["research"])])
    assert [(c.agent_name, c.rank) for c in ranked] == [("Alpha", 1), ("beta", 2)]
    assert _factors(ranked[0])["skill_fit"].detail == "The task lists no required skills."


def test_usable_tools_add_points() -> None:
    tools = [{"tool": "web_search", "access": "read"}, {"tool": "test_runner", "access": "write"}]
    ranked = rank_candidates(
        [],
        [_agent("Tooled", ["research"], tools=tools), _agent("Plain", ["research"])],
        registry=TEST_REGISTRY,
    )
    tooled = _factors(ranked[0])
    assert ranked[0].agent_name == "Tooled"
    assert tooled["tool_fit"].points == 10.0
    assert tooled["tool_fit"].detail == "1 of its 2 granted tools are usable now."
    assert _factors(ranked[1])["tool_fit"].detail == "It is granted no tools."


def test_unmeasured_factors_are_reported_as_not_scored() -> None:
    (candidate,) = rank_candidates([], [_agent("Writer", ["report_writing"])])
    factors = _factors(candidate)
    for name in ("history", "cost", "latency", "policy"):
        assert factors[name].outcome == "not_scored" and factors[name].points == 0.0
    assert "verification" in factors["history"].detail
    assert "Phase 7" in factors["cost"].detail
