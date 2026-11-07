"""Platform catalog of agent capabilities and tools.

This is platform policy, not agent data: an agent configuration may only reference entries
listed here. Granting a tool records a permission; nothing is executed by the registry, and
the tool gateway (Phase 3) decides at run time whether a tool is actually available.
"""

from dataclasses import dataclass
from enum import StrEnum


class ToolRisk(StrEnum):
    LOW = "LOW"  # reads or writes workspace artifacts only
    MEDIUM = "MEDIUM"  # reaches external systems (network)
    HIGH = "HIGH"  # executes code; only an OWNER may grant it


@dataclass(frozen=True)
class ToolSpec:
    name: str
    risk: ToolRisk
    description: str
    # Code execution stays disabled in Phase 1 (handoff section 13).
    available_in_phase_1: bool


TOOLS: dict[str, ToolSpec] = {
    spec.name: spec
    for spec in (
        ToolSpec(
            "document_retrieval", ToolRisk.LOW, "Read documents attached to the workflow.", True
        ),
        ToolSpec(
            "workspace_files_read", ToolRisk.LOW, "Read files in the workflow workspace.", True
        ),
        ToolSpec("artifact_writer", ToolRisk.LOW, "Write output artifacts.", True),
        ToolSpec(
            "workspace_files_write", ToolRisk.LOW, "Write files in the workflow workspace.", True
        ),
        ToolSpec("web_search", ToolRisk.MEDIUM, "Query an approved search provider.", True),
        ToolSpec("python_analysis", ToolRisk.HIGH, "Run Python in an isolated sandbox.", False),
        ToolSpec("test_runner", ToolRisk.HIGH, "Run tests in an isolated sandbox.", False),
    )
}

CAPABILITIES: frozenset[str] = frozenset(
    {
        "research",
        "source_extraction",
        "comparison",
        "evidence_summary",
        "analysis",
        "calculation",
        "data_interpretation",
        "code_generation",
        "debugging",
        "implementation",
        "report_writing",
        "documentation",
        "editing",
        "test_design",
        "test_execution",
        "acceptance_validation",
        "general_task",
        "coordination_support",
    }
)

CATEGORIES: frozenset[str] = frozenset(
    {"research", "analysis", "development", "writing", "qa", "general", "custom"}
)


def highest_tool_risk(tool_names: list[str]) -> ToolRisk | None:
    order = [ToolRisk.LOW, ToolRisk.MEDIUM, ToolRisk.HIGH]
    risks = [TOOLS[name].risk for name in tool_names if name in TOOLS]
    return max(risks, key=order.index) if risks else None
