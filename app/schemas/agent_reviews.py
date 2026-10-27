from typing import Literal

from pydantic import Field, field_validator

from app.schemas.company import StrictModel


class Citation(StrictModel):
    source_id: str = Field(min_length=1, max_length=100)
    quote: str = Field(min_length=5, max_length=1000)


class Requirement(StrictModel):
    requirement: str = Field(min_length=1, max_length=1000)
    category: Literal["eligibility", "technical", "financial", "documents", "submission", "other"]
    mandatory: bool | None
    citations: list[Citation] = Field(min_length=1, max_length=5)


class EligibilityFinding(StrictModel):
    criterion: str = Field(min_length=1, max_length=300)
    status: Literal["meets", "gap", "unknown"]
    explanation: str = Field(min_length=1, max_length=1500)
    citations: list[Citation] = Field(min_length=1, max_length=5)


class ExaminationReport(StrictModel):
    summary: str = Field(min_length=1, max_length=4000)
    recommendation: Literal["pursue", "review", "decline"]
    rationale: list[str] = Field(min_length=1, max_length=15)
    requirements: list[Requirement] = Field(max_length=40)
    eligibility: list[EligibilityFinding] = Field(max_length=30)
    missing_documents: list[str] = Field(max_length=30)
    questions: list[str] = Field(max_length=30)
    next_actions: list[str] = Field(max_length=30)
    limitations: list[str] = Field(max_length=20)

    @field_validator("rationale", "missing_documents", "questions", "next_actions", "limitations")
    @classmethod
    def bounded_text(cls, values: list[str]) -> list[str]:
        if any(not value.strip() or len(value) > 1500 for value in values):
            raise ValueError("Report items must contain 1–1500 characters")
        return values


class ReviewDecision(StrictModel):
    decision: Literal["accepted", "rejected"]
    note: str = Field(min_length=1, max_length=2000)

    @field_validator("note")
    @classmethod
    def nonblank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Describe the human review")
        return value.strip()
