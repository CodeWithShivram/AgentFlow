"""A harmless, deterministic agent: no model call, no tools. Used until the specialist agents
(Step 3.4) exist, and in tests."""

from app.agents.base import AgentContext, BaseAgent
from app.agents.contract import AgentSubmission, CriterionClaim, CriterionStatus, TaskContract


class MockAgent(BaseAgent):
    def execute(self, contract: TaskContract, context: AgentContext) -> AgentSubmission:
        attempt = contract.retry_policy.attempt_number
        return AgentSubmission(
            summary=(
                f"Mock result for task '{contract.objective}' "
                f"(agent version {context.agent_version}, attempt {attempt})."
            ),
            outputs={name: f"Mock content for: {name}" for name in contract.deliverables},
            criteria=[
                CriterionClaim(
                    criterion_id=criterion.id,
                    status=CriterionStatus.MET,
                    evidence="Mock agent claim; no real work was performed.",
                )
                for criterion in contract.acceptance_criteria
            ],
            limitations=["Mock agent: no model or tool was used."],
        )
