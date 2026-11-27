"""ui preview artifacts

Allows artifacts of kind UI_PREVIEW (Step 9.10, ADR-0057): the screenshot a web project's
integration run takes of its page in the sandbox, shown on the project page.

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-27 16:30:00.000000+00:00
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD = ("PROJECT_ARCHIVE", "BUILD_REPORT", "TASK_OUTPUT")
NEW = (*OLD, "UI_PREVIEW")


def _in(values: tuple[str, ...]) -> str:
    return "kind IN (" + ", ".join(f"'{value}'" for value in values) + ")"


def upgrade() -> None:
    op.drop_constraint(op.f("ck_artifacts_kind"), "artifacts", type_="check")
    op.create_check_constraint(op.f("ck_artifacts_kind"), "artifacts", _in(NEW))


def downgrade() -> None:
    op.execute("DELETE FROM artifacts WHERE kind = 'UI_PREVIEW'")
    op.drop_constraint(op.f("ck_artifacts_kind"), "artifacts", type_="check")
    op.create_check_constraint(op.f("ck_artifacts_kind"), "artifacts", _in(OLD))
