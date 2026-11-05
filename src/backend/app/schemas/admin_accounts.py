"""Administrator account-management request and response contracts."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from app.schemas.auth import Role
from app.schemas.common import PaginationParams

Gender = Literal["MALE", "FEMALE", "OTHER", "UNDISCLOSED"]


def normalize_email(value: str) -> str:
    normalized = value.strip().lower()
    if (
        not 1 <= len(normalized) <= 254
        or normalized.count("@") != 1
        or any(character.isspace() for character in normalized)
        or not normalized.partition("@")[0]
        or not normalized.partition("@")[2]
        or "." not in normalized.partition("@")[2]
    ):
        raise ValueError("Invalid email")
    return normalized


class AccountQuery(PaginationParams):
    model_config = ConfigDict(extra="forbid")

    role: Role | None = None
    is_active: bool | None = None
    search: str | None = Field(default=None, min_length=1, max_length=100)
    page: int = Field(default=1, ge=1, le=1_000_000)

    @field_validator("search")
    @classmethod
    def normalize_search(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Search must not be blank")
        return normalized


class AccountCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    email: str
    name: str = Field(min_length=1, max_length=150)
    role: Role
    password: SecretStr = Field(min_length=1, max_length=1024)
    is_active: bool = True
    student_code: str | None = Field(default=None, max_length=50)
    faculty: str | None = Field(default=None, max_length=150)
    major: str | None = Field(default=None, max_length=150)
    gender: Gender | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        return normalize_email(value)

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Name must not be blank")
        return normalized

    @field_validator("student_code", "faculty", "major")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_role_profile(self) -> "AccountCreateRequest":
        if self.role != "STUDENT" and any(
            value is not None
            for value in (self.student_code, self.faculty, self.major, self.gender)
        ):
            raise ValueError("Student profile fields require the STUDENT role")
        return self


class AccountUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_updated_at: AwareDatetime
    email: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=150)
    role: Role | None = None
    is_active: bool | None = None
    student_code: str | None = Field(default=None, max_length=50)
    faculty: str | None = Field(default=None, max_length=150)
    major: str | None = Field(default=None, max_length=150)
    gender: Gender | None = None

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        return normalize_email(value) if value is not None else None

    @field_validator("name")
    @classmethod
    def normalize_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("Name must not be blank")
        return normalized

    @field_validator("student_code", "faculty", "major")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_changes(self) -> "AccountUpdateRequest":
        if not (self.model_fields_set - {"expected_updated_at"}):
            raise ValueError("At least one account field must be supplied")
        for field in ("email", "name", "role", "is_active"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} cannot be null")
        if (
            self.role is not None
            and self.role != "STUDENT"
            and any(
                value is not None
                for value in (self.student_code, self.faculty, self.major, self.gender)
            )
        ):
            raise ValueError("Student profile fields require the STUDENT role")
        return self


class PasswordResetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_updated_at: AwareDatetime
    password: SecretStr = Field(min_length=1, max_length=1024)


class AccountItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    email: str
    name: str
    role: Role
    is_active: bool
    student_code: str | None
    faculty: str | None
    major: str | None
    gender: Gender | None
    created_at: datetime
    updated_at: datetime
