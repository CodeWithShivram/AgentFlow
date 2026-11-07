import uuid
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, SecretStr

from app.models import WorkspaceRole


def _normalize_email(value: str) -> str:
    return value.strip().lower()


def _not_blank(value: SecretStr) -> SecretStr:
    if not value.get_secret_value().strip():
        raise ValueError("password must not be blank")
    return value


NormalizedEmail = Annotated[EmailStr, Field(max_length=320), AfterValidator(_normalize_email)]
# Length is checked here; Argon2id makes hashing cost independent of password content.
NewPassword = Annotated[SecretStr, Field(min_length=12, max_length=128), AfterValidator(_not_blank)]


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: NormalizedEmail
    password: NewPassword
    workspace_name: str | None = Field(default=None, min_length=1, max_length=200)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: NormalizedEmail
    password: SecretStr = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    created_at: datetime


class MembershipResponse(BaseModel):
    workspace_id: uuid.UUID
    workspace_name: str
    role: WorkspaceRole


class MeResponse(BaseModel):
    user: UserResponse
    memberships: list[MembershipResponse]


class SessionResponse(MeResponse):
    """Returned by register, login and refresh. Tokens are set as cookies, never in the body."""

    access_expires_at: datetime
    session_expires_at: datetime
