"""Project packages (ADR-0052): each attempt to package a software project's deliverable.

READY rows point at the stored archive and build report (`artifacts`); FAILED rows keep a fixed
code (e.g. SECRET_DETECTED) and no archive. The label says what the package is: VERIFIED (its
commit passed the integration gate) or PARTIAL (packaged on an admin's explicit request without
a passing gate). Append-only.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, created_at_column, str_enum, uuid_pk


class PackageStatus(StrEnum):
    READY = "READY"
    FAILED = "FAILED"


class PackageLabel(StrEnum):
    VERIFIED = "VERIFIED"
    PARTIAL = "PARTIAL"


class ProjectPackage(Base):
    __tablename__ = "project_packages"
    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "workflow_id"],
            ["workflows.workspace_id", "workflows.id"],
            ondelete="CASCADE",
            name="fk_project_packages_workflow",
        ),
        CheckConstraint(
            "(status = 'READY') = (archive_artifact_id IS NOT NULL AND error_code IS NULL)",
            name="ready_has_archive",
        ),
        CheckConstraint(
            "label = 'PARTIAL' OR integration_id IS NOT NULL", name="verified_has_integration"
        ),
        Index("ix_project_packages_workflow_created", "workflow_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    workspace_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"))
    workflow_id: Mapped[uuid.UUID]
    # The passing integration of the packaged commit (NULL only for PARTIAL packages).
    integration_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("project_integrations.id", ondelete="CASCADE")
    )
    head_sha: Mapped[str] = mapped_column(String(40))
    label: Mapped[PackageLabel] = mapped_column(str_enum(PackageLabel, "label"))
    status: Mapped[PackageStatus] = mapped_column(str_enum(PackageStatus, "status"))
    error_code: Mapped[str | None] = mapped_column(String(64))
    archive_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE")
    )
    report_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="CASCADE")
    )
    file_count: Mapped[int] = mapped_column(default=0, server_default="0")
    excluded_count: Mapped[int] = mapped_column(default=0, server_default="0")
    # A person who asked for a PARTIAL package.
    requested_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = created_at_column()
