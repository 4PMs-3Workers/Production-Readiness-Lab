"""Validate the reviewed manifest; UUID allocation never synthesizes content."""

from collections import Counter
from datetime import time
from importlib.resources import files
from typing import Literal
from uuid import UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, ValidationError

NAMESPACE = UUID("9a15c7ab-9dec-498d-b37a-ad8d777d840e")
PURPOSE_CODES = {
    "CAREER_INTERNSHIP",
    "THESIS_RESEARCH",
    "ACADEMIC_ADVISOR",
    "GENERAL_ADVICE",
    "OTHER",
}


class SeedError(ValueError):
    """Safe, value-free diagnostic for the command line."""


def seed_id(table: str, key: str) -> UUID:
    return uuid5(NAMESPACE, f"{table}:{key}")


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Account(Record):
    name: str = Field(min_length=1, max_length=150)
    email: str = Field(min_length=1, max_length=254)
    role: Literal["ADMIN", "STAFF", "STUDENT"]
    is_active: Literal[True]


class Consultation(Record):
    code: str
    name: str = Field(min_length=1, max_length=150)
    duration_minutes: int = Field(gt=0, le=32767)
    requires_free_text: bool
    is_active: Literal[True]


class Assignment(Record):
    staff_email: str
    purpose_code: str
    is_accepting: Literal[True]


class BookingSettings(Record):
    id: Literal[1]
    timezone: Literal["Asia/Ho_Chi_Minh"]
    start_step_minutes: Literal[15]
    booking_horizon_days: Literal[7]


class Window(Record):
    weekday: int = Field(ge=1, le=7)
    opens_at: time
    closes_at: time

    @property
    def key(self) -> str:
        return f"{self.weekday}/{self.opens_at.isoformat()}/{self.closes_at.isoformat()}"


class Dataset(Record):
    accounts: list[Account]
    purposes: list[Consultation]
    assignments: list[Assignment]
    settings: BookingSettings
    hours: list[Window]

    def review(self) -> None:
        counts = Counter(account.role for account in self.accounts)
        if counts != {"ADMIN": 2, "STAFF": 10, "STUDENT": 30}:
            raise SeedError("Dataset: expected 2 ADMIN, 10 STAFF and 30 STUDENT accounts")
        emails = {account.email for account in self.accounts}
        if len(emails) != 42 or len({a.name for a in self.accounts}) != 42:
            raise SeedError("Dataset: duplicate account identity")
        for account in self.accounts:
            if (
                account.name != account.name.strip()
                or not account.name.strip()
                or account.email != account.email.strip().lower()
                or not account.email.endswith("@demo.example.invalid")
                or account.email.count("@") != 1
                or not account.email.split("@")[0]
                or any(c.isspace() for c in account.email)
            ):
                raise SeedError("Dataset: invalid account identity")
        codes = {purpose.code for purpose in self.purposes}
        if codes != PURPOSE_CODES or len(self.purposes) != 5:
            raise SeedError("Dataset: expected the five consultation codes")
        for purpose in self.purposes:
            if not purpose.name.strip() or (
                purpose.code == "OTHER" and not purpose.requires_free_text
            ):
                raise SeedError("Dataset: invalid consultation content")
        staff = {a.email for a in self.accounts if a.role == "STAFF"}
        pairs = {(a.staff_email, a.purpose_code) for a in self.assignments}
        if (
            len(pairs) != len(self.assignments)
            or {email for email, _ in pairs} != staff
            or {code for _, code in pairs} != codes
        ):
            raise SeedError("Dataset: invalid staff-purpose coverage or duplicate association")
        if not self.hours or len({w.key for w in self.hours}) != len(self.hours):
            raise SeedError("Dataset: missing or duplicate service windows")
        for index, window in enumerate(self.hours):
            if (
                window.opens_at.tzinfo is not None
                or window.closes_at.tzinfo is not None
                or window.opens_at >= window.closes_at
            ):
                raise SeedError("Dataset: invalid service interval")
            for other in self.hours[index + 1 :]:
                if overlaps(window, other):
                    raise SeedError("Dataset: overlapping service windows")


def overlaps(first: Window, second: Window) -> bool:
    return (
        first.weekday == second.weekday
        and first.opens_at < second.closes_at
        and second.opens_at < first.closes_at
    )


def load_dataset() -> Dataset:
    try:
        dataset = Dataset.model_validate_json(
            files("app.demo").joinpath("dataset.json").read_text(encoding="utf-8")
        )
    except (ValidationError, OSError, ValueError):
        raise SeedError("Dataset: invalid reviewed manifest") from None
    dataset.review()
    return dataset
