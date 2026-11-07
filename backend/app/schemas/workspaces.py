import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from app.models import WorkspaceRole
from app.schemas.auth import NormalizedEmail


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class WorkspaceResponse(BaseModel):
    id: uuid.UUID
    name: str
    owner_id: uuid.UUID
    # The caller's role in this workspace.
    role: WorkspaceRole
    created_at: datetime


class MemberResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    role: WorkspaceRole
    joined_at: datetime


class InvitationCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: NormalizedEmail
    role: Literal["ADMIN", "MEMBER", "VIEWER"]


class InvitationCreatedResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    email: str
    role: WorkspaceRole
    expires_at: datetime
    # Returned once so the inviter can share it; only its hash is stored. Email delivery
    # is not part of the MVP.
    invitation_token: str


class InvitationAcceptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: SecretStr = Field(min_length=1, max_length=200)


class AcceptedMembershipResponse(BaseModel):
    workspace_id: uuid.UUID
    workspace_name: str
    role: WorkspaceRole
