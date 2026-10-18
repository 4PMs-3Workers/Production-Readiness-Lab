"""Public authentication HTTP schemas; submitted passwords are never echoed."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

Role = Literal["STUDENT", "STAFF", "ADMIN"]


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    email: str
    password: SecretStr = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if not 1 <= len(value) <= 254:
            raise ValueError("Invalid email")
        return value


class PublicUser(BaseModel):
    id: UUID
    email: str
    name: str
    role: Role


class CsrfResponse(BaseModel):
    csrf_token: str
    user_id: UUID
