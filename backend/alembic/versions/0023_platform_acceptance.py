"""platform acceptance

Only platform verification accepts tasks (Phase 5, Step 5.5, ADR-0034): a trigger refuses any
write that makes a task COMPLETED unless its latest successful attempt has PASSED results from
both the deterministic checks and the semantic evaluation. The engine checks the same first.

Revision ID: 0023
Revises: 0022
Create Date: 2026-11-15 22:00:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE FUNCTION workflow_tasks_require_acceptance() RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            latest uuid;
        BEGIN
            IF NEW.status <> 'COMPLETED'
               OR (TG_OP = 'UPDATE' AND OLD.status = 'COMPLETED') THEN
                RETURN NEW;
            END IF;
            SELECT e.id INTO latest
              FROM agent_executions e
             WHERE e.task_id = NEW.id AND e.status = 'SUCCEEDED'
             ORDER BY e.attempt_number DESC
             LIMIT 1;
            IF latest IS NULL OR (
                SELECT count(DISTINCT split_part(v.verifier, '/', 1))
                  FROM verification_results v
                 WHERE v.execution_id = latest
                   AND v.outcome = 'PASSED'
                   AND split_part(v.verifier, '/', 1) IN ('deterministic', 'semantic')
            ) < 2 THEN
                RAISE EXCEPTION 'only platform verification can complete a task'
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        "CREATE TRIGGER workflow_tasks_platform_acceptance "
        "BEFORE INSERT OR UPDATE OF status ON workflow_tasks "
        "FOR EACH ROW EXECUTE FUNCTION workflow_tasks_require_acceptance()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER workflow_tasks_platform_acceptance ON workflow_tasks")
    op.execute("DROP FUNCTION workflow_tasks_require_acceptance()")
